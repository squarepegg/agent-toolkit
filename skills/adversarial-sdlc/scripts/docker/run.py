#!/usr/bin/env python3
"""Docker backend for sandbox.sh (spec: docs/specs/docker-backend.md).

  run.py [--rw DIR]... [--ro DIR]... [--deny DIR]... [--net all|local|none] -- COMMAND [ARGS]
  run.py --check        exit 0 if the backend is usable (daemon, images, cache volume), else 1
  run.py --ids          print the pinned image IDs (images.env lines)

sandbox.sh execs this on the host after choosing the docker backend. There is one container per
call, and it is always removed. --net all also gets a private internal network and an egress
proxy container, both removed afterwards. A detached reaper process removes them if this
process is SIGKILLed (INV-6). Exit codes: the container's own exit code; 97 if the sandbox
cannot be set up (FM-1, FM-2, FM-9, A-3); 2 for bad arguments.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
import signal
import subprocess
import sys
import time
import urllib.parse
from pathlib import Path
from typing import NoReturn

HERE = Path(__file__).resolve().parent
SKILL_DIR = HERE.parent.parent
VOLUME = os.environ.get("SDLC_DOCKER_VOLUME", "sdlc-gradle-ro")  # same default as warmup.sh


def docker_context() -> str:
    """Image IDs are per daemon, so the recorded state is kept per Docker context."""
    ctx = os.environ.get("DOCKER_CONTEXT")
    if not ctx:
        try:
            ctx = subprocess.run(["docker", "context", "show"], capture_output=True, text=True,
                                 check=False).stdout.strip()
        except FileNotFoundError:
            ctx = ""
    return re.sub(r"[^A-Za-z0-9_.-]", "_", ctx or "default")


STATE = Path(os.environ.get("SDLC_DOCKER_STATE")
             or Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "sdlc-docker" / docker_context())
C_HOME, C_TMP = "/home/sdlc", "/tmp"  # noqa: S108 - container paths, both private tmpfs mounts
NEVER_START = "0001-01-01T00:00:00Z"


def die(msg: str, code: int = 97) -> NoReturn:
    print(f"sandbox.sh (docker): {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def docker(*args: str, check: bool = True, quiet: bool = True) -> subprocess.CompletedProcess:
    p = subprocess.run(["docker", *args], capture_output=quiet, text=True, check=False)
    if check and p.returncode != 0:
        die(f"docker {args[0]} failed: {(p.stderr or '').strip()[:500]}")
    return p


# ------------------------------------------------------------------ images and volume

def image_ids() -> dict[str, str]:
    """Pinned image IDs: an explicit env override (resolved to its ID) wins over images.env."""
    ids: dict[str, str] = {}
    f = STATE / "images.env"
    if f.is_file():
        for line in f.read_text().splitlines():
            m = re.match(r"^SDLC_DOCKER_ID_(GATE|ROLE|EGRESS)=(sha256:[0-9a-f]{64})$", line.strip())
            if m:
                ids[m.group(1).lower()] = m.group(2)
    for kind in ("gate", "role", "egress"):
        ref = os.environ.get(f"SDLC_DOCKER_IMAGE_{kind.upper()}")
        if ref:
            p = docker("image", "inspect", "--format", "{{.Id}}", ref, check=False)
            if p.returncode != 0:
                die(f"image not found: SDLC_DOCKER_IMAGE_{kind.upper()}={ref}")
            ids[kind] = p.stdout.strip()
    return ids


def check() -> str | None:
    """None if usable, else the reason. One docker call on the fast path (`docker info` takes seconds)."""
    ids = image_ids()
    missing = [k for k in ("gate", "role", "egress") if k not in ids]
    if missing:
        return f"image not built ({', '.join(missing)}): run scripts/docker/build.sh"
    try:
        if docker("inspect", "--format", "ok", *ids.values(), VOLUME, check=False).returncode == 0:
            return None
        if docker("version", "--format", "{{.Server.Version}}", check=False).returncode != 0:
            return "Docker daemon not reachable"
    except FileNotFoundError:
        return "docker CLI not installed"
    if docker("image", "inspect", *ids.values(), check=False).returncode != 0:
        return "image digest mismatch (pinned image missing): run scripts/docker/build.sh"
    return f"Gradle cache volume {VOLUME} missing: run scripts/docker/build.sh"


# ------------------------------------------------------------------ arguments and mounts

# real path -> other absolute spellings callers used (macOS: /var/folders -> /private/var/folders,
# /tmp -> /private/tmp). Each is mounted too, so the caller's paths work inside the container.
ALIASES: dict[str, set[str]] = {}


def real(p: str) -> str:
    r, a = os.path.realpath(p), os.path.abspath(p)
    if any(c in r + a for c in "\n\r\t\0"):
        die(f"refusing a path with control characters: {r!r}", 2)
    if a != r:
        ALIASES.setdefault(r, set()).add(a)
    return r


def under(child: str, parent: str) -> bool:
    return child == parent or child.startswith(parent.rstrip("/") + "/")


def mount_arg(path: str, ro: bool, dst: str | None = None) -> str:
    """--mount value with CSV quoting, so commas and quotes in paths cannot inject options (FM-12)."""
    q = lambda s: '"' + s.replace('"', '""') + '"'  # noqa: E731
    return ",".join(["type=bind", q(f"src={path}"), q(f"dst={dst or path}")] + (["readonly"] if ro else []))


def listing_sha(path: str) -> str:
    sys.path.insert(0, str(HERE))
    from entry import listing_sha as ls  # same function the container runs
    return ls(path)


def parse(argv: list[str]):
    rw: list[str] = []
    ro: list[str] = []
    deny: list[str] = []
    net = os.environ.get("SDLC_SANDBOX_NET", "all")
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--":
            return rw, ro, deny, net, argv[i + 1:]
        if a in ("--rw", "--ro", "--deny", "--net") and i + 1 < len(argv):
            v = argv[i + 1]
            if a == "--rw":
                os.makedirs(v, exist_ok=True)
                rw.append(real(v))
            elif a == "--ro":
                if not os.path.exists(v):
                    die(f"--ro path does not exist: {v}", 2)
                ro.append(real(v))
            elif a == "--deny":
                if os.path.exists(v):
                    deny.append(real(v))
            else:
                net = v
            i += 2
            continue
        die(f"unknown argument {a}", 2)
    return rw, ro, deny, net, []


# ------------------------------------------------------------------ cleanup

class Run:
    def __init__(self) -> None:
        self.rid = secrets.token_hex(6)
        self.container = f"sdlc-run-{self.rid}"
        self.egress = f"sdlc-egress-{self.rid}"
        self.network = f"sdlc-net-{self.rid}"
        self.nonces: list[Path] = []
        self.reaper: subprocess.Popen | None = None
        self.cleaned = False
        self.clean_ok = False

    def start_reaper(self) -> None:
        # Detached (own session), so killing our process group does not kill it.
        self.reaper = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "--reap", str(os.getpid()),
             self.container, self.egress, self.network],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            start_new_session=True)

    def cleanup(self) -> bool:
        """Remove the containers and network; False unless their removal is confirmed (the caller fails with 97).
        The reaper is stopped only on success, so it can still retry after this process exits."""
        if self.cleaned:
            return self.clean_ok
        self.cleaned = True
        self.clean_ok = remove(self.container, self.egress, self.network)
        for n in self.nonces:
            with contextlib.suppress(OSError):
                n.unlink()
        if self.reaper and self.clean_ok:
            self.reaper.terminate()
        return self.clean_ok


def remove(container: str, egress: str, network: str) -> bool:
    """True when the containers and the network are gone."""
    subprocess.run(["docker", "rm", "-f", container, egress], capture_output=True, check=False)
    gone = False
    for _ in range(20):  # fails while the removed containers' endpoints are still detaching
        p = subprocess.run(["docker", "network", "rm", network], capture_output=True, text=True, check=False)
        if p.returncode == 0 or "not found" in p.stderr.lower():
            gone = True
            break
        time.sleep(0.5)
    left = subprocess.run(["docker", "ps", "-aq", "--filter", f"name=^{container}$", "--filter", f"name=^{egress}$"],
                          capture_output=True, text=True, check=False)
    return gone and left.returncode == 0 and not left.stdout.strip()


def reap(parent: int, container: str, egress: str, network: str) -> None:
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))  # the parent cleaned up itself
    while True:
        try:
            os.kill(parent, 0)
        except ProcessLookupError:
            break
        except PermissionError:
            time.sleep(1)  # alive, but owned by someone else (pid reuse): keep waiting
            continue
        time.sleep(1)
    remove(container, egress, network)


# ------------------------------------------------------------------ main

def gateway_upstream() -> tuple[str, str]:
    """(egress upstream host:port, in-container gateway URL) from the host's gateway URL."""
    url = os.environ.get("SDLC_GATEWAY_URL") or "http://localhost:8080"
    u = urllib.parse.urlsplit(url)
    if u.scheme != "http" or not u.hostname:
        die(f"gateway URL must be http://host:port for the docker backend (got {url})")
    host = u.hostname
    if host in ("localhost", "127.0.0.1", "::1"):
        host = os.environ.get("SDLC_DOCKER_HOST_ALIAS", "host.docker.internal")
    port = u.port or 80
    up = os.environ.get("SDLC_EGRESS_UPSTREAM") or f"{host}:{port}"
    return up, f"http://sdlc-egress:{up.rpartition(':')[2]}{u.path.rstrip('/')}"


def pi_settings() -> dict:
    try:
        src = json.loads((Path.home() / ".pi/agent/settings.json").read_text())
    except (OSError, ValueError):
        src = {}
    return {k: src[k] for k in ("defaultProvider", "defaultModel", "defaultThinkingLevel") if k in src}


def main(argv: list[str]) -> int:
    if argv[:1] == ["--check"]:
        why = check()
        if why and "-v" in argv:
            print(why, file=sys.stderr)
        return 0 if why is None else 1
    if argv[:1] == ["--ids"]:
        for k, v in sorted(image_ids().items()):
            print(f"SDLC_DOCKER_ID_{k.upper()}={v}")
        return 0
    if argv[:1] == ["--reap"]:
        reap(int(argv[1]), *argv[2:5])
        return 0

    rw, ro, deny, net, cmd = parse(argv)
    if not cmd:
        die("no command", 2)
    if net not in ("all", "local", "none"):
        die("--net must be all|local|none", 2)
    role = net == "all"
    # sandbox.sh has just run --check. If anything vanished since, `docker create` fails, and that is a 97.
    why = None if os.environ.pop("SDLC_DOCKER_CHECKED", "") == "1" else check()
    if why:
        die(why)
    ids = image_ids()

    # Mounts: the caller's, plus read-only skill dir (A-1), SDLC_SANDBOX_READ and, for model roles,
    # pi extensions (A-6). rw wins over ro for the same path. Nothing else is visible (INV-1).
    extra = [str(SKILL_DIR)] + os.environ.get("SDLC_SANDBOX_READ", "").split()
    if role:
        extra += os.environ.get("SDLC_PI_EXTENSIONS", "").split()
    ro += [real(p) for p in extra if p and os.path.exists(p)]
    mounts: dict[str, str] = {}
    for p in ro:
        mounts.setdefault(p, "ro")
    for p in rw:
        mounts[p] = "rw"
    home = real(str(Path.home()))
    for p in mounts:
        if under(home, p) or p in ("/", "/Users", "/Volumes", "/private", "/home"):
            die(f"refusing to mount {p}: it contains the home directory or a system root", 2)
    for d in deny:
        for p in mounts:
            if under(d, p):
                die(f"denied dir {d} is inside mounted dir {p}", 2)

    run = Run()

    def on_signal(sig, _frame):
        os._exit(128 + sig if run.cleanup() else 97)
    for s in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(s, on_signal)
    run.start_reaper()
    rc = 97
    try:
        rc = launch(run, ids, mounts, net, role, cmd)
    finally:
        if not run.cleanup():
            print(f"sandbox.sh (docker): could not confirm that container {run.container} is gone", file=sys.stderr)
            rc = 97
    return rc


def role_cpus(role: bool) -> str:
    """CPUs for the container. A model role needs little; when code under test runs on Kubernetes
    (SDLC_SANDBOX_BACKEND_CODE=k8s), its builds and tests go to its warm pod there (sdlc-check), so a role container
    is capped (SDLC_DOCKER_ROLE_CPUS, default 2): a role that ignores its task and builds here cannot load the laptop."""
    if role and os.environ.get("SDLC_SANDBOX_BACKEND_CODE") == "k8s":
        return os.environ.get("SDLC_DOCKER_ROLE_CPUS", "2")
    return os.environ.get("SDLC_DOCKER_CPUS", "4")


ROLE_ENV_DENY = re.compile(r"(_TOKEN|_KEY|_SECRET|_PASSWORD|_PASSWD|_CREDENTIALS?)$|^SDLC_NOTIFY_CMD$")


def forwarded_env(fixed: dict[str, str], role: bool) -> dict[str, str]:
    """Host variables passed into the container beyond the fixed ones. Code under test (--net local|none) gets only
    what SDLC_DOCKER_PASSENV names: a blanket SDLC_*/GRADLE_* forward would hand it whatever credentials live
    there (GRADLE_ENTERPRISE_ACCESS_KEY, ...). Model roles run the harness, which reads its SDLC_* settings; they get
    those, but no GRADLE_* and no SDLC_* name that looks like a credential or a command (a role can send anything it
    reads to the model provider)."""
    passenv = set(os.environ.get("SDLC_DOCKER_PASSENV", "").split())
    out: dict[str, str] = {}
    for k, v in os.environ.items():
        if k in fixed or k in ("SDLC_PROJECT", "SDLC_DOCKER_ENTRY", "SDLC_DOCKER_PASSENV", "SDLC_IN_SANDBOX",
                               "GRADLE_HOME"):  # GRADLE_HOME is a host path
            continue
        # ast-grep-ignore: env-forward-by-prefix -- roles need the harness's settings; credential-like names are excluded
        if k in passenv or (role and k.startswith("SDLC_") and not ROLE_ENV_DENY.search(k)):
            out[k] = v
    return out


def launch(run: Run, ids: dict[str, str], mounts: dict[str, str], net: str, role: bool,
           cmd: list[str]) -> int:
    plan: dict = {"mounts": [], "gradle_dists": False, "pi_settings": pi_settings() if role else {}}
    for p, mode in sorted(mounts.items()):
        if mode == "rw":
            nonce = f".sdlc-mount-check-{run.rid}"
            (Path(p) / nonce).write_text("")
            run.nonces.append(Path(p) / nonce)
            plan["mounts"].append({"path": p, "mode": "rw", "nonce": nonce})
        else:
            plan["mounts"].append({"path": p, "mode": "ro", "listing": listing_sha(p)})
        if (Path(p) / "gradle/wrapper/gradle-wrapper.properties").is_file():
            plan["gradle_dists"] = True

    env = {
        "HOME": C_HOME, "TMPDIR": C_TMP, "LANG": "C.UTF-8",
        "GRADLE_USER_HOME": f"{C_HOME}/.gradle", "GRADLE_RO_DEP_CACHE": "/opt/gradle-ro/caches",
        "PI_CODING_AGENT_DIR": f"{C_HOME}/.pi/agent",
        # gate.config pins its JDK via macOS java_home, else GATE_JAVA_HOME: the image has JDK 21 only.
        "GATE_JAVA_HOME": "/usr/lib/jvm/sdlc-java-21",
        "JAVA_TOOL_OPTIONS": f"-Djava.io.tmpdir={C_TMP} -Duser.home={C_HOME} "
                             + os.environ.get("JAVA_TOOL_OPTIONS", ""),
        "SDLC_DOCKER_ENTRY": json.dumps(plan),
    }
    env.update(forwarded_env(env, role))
    for k, v in ids.items():
        env[f"SDLC_DOCKER_ID_{k.upper()}"] = v

    netargs = ["--network", "none"]
    if role:
        upstream, url = gateway_upstream()
        proxy = "http://sdlc-egress:8080"
        # The provider extension may read the gateway URL under its own name: SDLC_GATEWAY_ENV lists the names.
        env.update({name: url for name in ["SDLC_GATEWAY_URL", *os.environ.get("SDLC_GATEWAY_ENV", "").split()]})
        env.update({"HTTP_PROXY": proxy, "HTTPS_PROXY": proxy,
                    "http_proxy": proxy, "https_proxy": proxy,
                    "NO_PROXY": "sdlc-egress,localhost,127.0.0.1", "no_proxy": "sdlc-egress,localhost,127.0.0.1"})
        start_egress(run, ids["egress"], upstream)
        netargs = ["--network", run.network]

    mem = os.environ.get("SDLC_DOCKER_MEMORY", "4g")
    create = ["create", "-i", "--name", run.container, "--label", "sdlc=run", "--init",
              "--read-only", "--cap-drop", "ALL", "--security-opt", "no-new-privileges",
              "--user", f"{os.getuid()}:{os.getgid()}", "--hostname", "sdlc",
              "--memory", mem, "--memory-swap", mem, "--cpus", role_cpus(role),
              "--pids-limit", os.environ.get("SDLC_DOCKER_PIDS", "4096"), *netargs,
              "--tmpfs", f"{C_TMP}:rw,exec,nosuid,nodev,size=2g,mode=1777",
              "--tmpfs", f"{C_HOME}:rw,exec,nosuid,nodev,size=2g,mode=1777",
              "--mount", f"type=volume,src={VOLUME},dst=/opt/gradle-ro,readonly"]
    for p, mode in sorted(mounts.items()):
        create += ["--mount", mount_arg(p, mode == "ro")]
        for a in sorted(ALIASES.get(p, ())):
            create += ["--mount", mount_arg(p, mode == "ro", a)]
    cwd = real(os.getcwd())
    create += ["--workdir", cwd if any(under(cwd, p) for p in mounts) else C_HOME]
    for k, v in sorted(env.items()):
        create += ["-e", f"{k}={v}"]
    image = ids["role"] if role else ids["gate"]
    create += [image, "python3", "-I", str(HERE / "entry.py"), *cmd]
    docker(*create)

    # stdin, stdout and stderr pass straight through (the caller pipes them into its log).
    subprocess.run(["docker", "start", "-ai", run.container], check=False)
    st = docker("inspect", "--format", "{{json .State}}", run.container, check=False)
    if st.returncode != 0:
        die("container vanished before its exit status was read")
    state = json.loads(st.stdout)
    if state.get("Error") or state.get("StartedAt") in (None, "", NEVER_START):
        die(f"container failed to start: {state.get('Error') or 'unknown error'}")
    if state.get("OOMKilled"):
        print(f"sandbox.sh (docker): container killed: out of memory (limit {mem})", file=sys.stderr)
    if role:
        logs = docker("logs", run.egress, check=False)
        for line in (logs.stderr + logs.stdout).splitlines():
            if "REFUSED" in line or "unreachable" in line:
                print(line, file=sys.stderr)
    return int(state.get("ExitCode", 1))


def start_egress(run: Run, image: str, upstream: str) -> None:
    docker("network", "create", "--internal", "--label", "sdlc=run", run.network)
    docker("run", "-d", "--name", run.egress, "--label", "sdlc=egress", "--read-only",
           "--cap-drop", "ALL", "--security-opt", "no-new-privileges", "--user", "10001:10001",
           "--memory", "256m", "--pids-limit", "64",
           "--add-host", "host.docker.internal:host-gateway",
           "-e", f"SDLC_EGRESS_UPSTREAM={upstream}",
           "-e", f"SDLC_EGRESS_ALLOW={os.environ.get('SDLC_EGRESS_ALLOW', upstream)}", image)
    docker("network", "connect", "--alias", "sdlc-egress", run.network, run.egress)
    for _ in range(200):
        logs = docker("logs", run.egress, check=False)
        if " ready on " in logs.stderr + logs.stdout:
            return
        if docker("inspect", "--format", "{{.State.Running}}", run.egress, check=False).stdout.strip() == "false":
            break
        time.sleep(0.05)
    logs = docker("logs", run.egress, check=False)
    die("egress proxy did not start: " + (logs.stderr + logs.stdout).strip()[-800:])


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
