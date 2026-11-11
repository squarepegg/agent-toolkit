#!/usr/bin/env python3
"""Check broker: a role's own checks (build, test, repro runs) run in one warm k8s runner pod per role, not on the laptop.

  broker.py serve --ws WORKSPACE [--role NAME]    host side, started by sdlc.sh around a role; stops on SIGTERM
                                                  or when WORKSPACE/.sdlc-remote/stop exists

The role runs `scripts/sdlc-check COMMAND...` in its container. That writes a request into WORKSPACE/.sdlc-remote/req/;
the broker syncs the workspace's changes into the pod, runs the command there and writes the output and exit code to
WORKSPACE/.sdlc-remote/res/. The pod lives as long as the role, with a Gradle daemon of its own, so a check after an
edit compiles incrementally.

Trust: requests are written by an untrusted role. They are size-capped and validated, the working directory must lie
in the workspace, and commands only ever run inside the pod (the same isolation as the gate's: no network, no
credentials, barrier probe first). The pod is reused across a role's checks, so code it runs can influence its later
checks: they are advisory, never evidence (speed spec S2.3). The orchestrator's gate runs in fresh pods, as before.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import signal
import sys
import tarfile
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as k8s  # noqa: E402 - the runner, next to this file

REMOTE = ".sdlc-remote"
# Not uploaded: build outputs (the pod has its own), the broker's own files.
SKIP_UP = (REMOTE, "build", ".gradle", ".kotlin", ".sdlc/build", "buildSrc/build", "buildSrc/.gradle")
# Copied back after each check: what roles read (reports, test results, the scratch gate's report).
KEEP_DEFAULT = "build/reports build/test-results .sdlc/build"
MAX_REQUEST = 64 * 1024
MAX_CMD = 16 * 1024
ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
STOP = False


def log(msg: str) -> None:
    print(f"broker: {time.strftime('%H:%M:%S')} {msg}", file=sys.stderr, flush=True)


# ---------------------------------------------------------------- requests (untrusted)

def parse_request(path: Path, ws: Path) -> dict:
    """A validated request, or ValueError. {"id", "cmd": str, "cwd": workspace-relative dir}."""
    if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_REQUEST:
        raise ValueError("not a regular file of at most 64 KiB")
    req = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(req, dict) or not isinstance(req.get("id"), str) or not ID.match(req["id"]):
        raise ValueError("bad id")
    if not isinstance(req.get("cmd"), str) or not req["cmd"].strip() or len(req["cmd"]) > MAX_CMD:
        raise ValueError("bad cmd")
    cwd = req.get("cwd", ".")
    if not isinstance(cwd, str) or cwd.startswith("/") or ".." in Path(cwd).parts:
        raise ValueError("cwd must be a relative path inside the workspace")
    return {"id": req["id"], "cmd": req["cmd"], "cwd": os.path.normpath(cwd)}


def write_response(ws: Path, rid: str, data: dict) -> None:
    res = ws / REMOTE / "res"
    tmp = res / f".{rid}.json.tmp"
    tmp.write_text(json.dumps(data))
    os.replace(tmp, res / f"{rid}.json")


# ---------------------------------------------------------------- workspace sync

def skipped(rel: str) -> bool:
    return any(rel == s or rel.startswith(s + "/") for s in SKIP_UP)


def snapshot(ws: Path) -> dict[str, tuple]:
    """Every uploadable entry: rel -> (kind, mode bits, size, mtime_ns or link target). Links are not followed."""
    out: dict[str, tuple] = {}
    for dirpath, dirnames, filenames in os.walk(ws):
        rd = os.path.relpath(dirpath, ws)
        dirnames[:] = sorted(d for d in dirnames if not skipped(os.path.normpath(os.path.join(rd, d))))
        for n in dirnames + sorted(filenames):
            rel = os.path.normpath(os.path.join(rd, n))
            if skipped(rel):
                continue
            st = os.lstat(os.path.join(dirpath, n))
            if os.path.islink(os.path.join(dirpath, n)):
                out[rel] = ("l", 0, 0, os.readlink(os.path.join(dirpath, n)))
            elif os.path.isdir(os.path.join(dirpath, n)):
                out[rel] = ("d", 0, 0, 0)
            elif os.path.isfile(os.path.join(dirpath, n)):
                out[rel] = ("f", 1 if st.st_mode & 0o100 else 0, st.st_size, st.st_mtime_ns)
    return out


def delta(old: dict[str, tuple], new: dict[str, tuple]) -> tuple[list[str], list[str]]:
    """(changed or new entries to upload, entries to delete in the pod), deletions deepest first."""
    changed = sorted(r for r, v in new.items() if old.get(r) != v)
    deleted = sorted((r for r in old if r not in new), key=lambda r: -r.count("/"))
    return changed, deleted


def pack_entries(ws: Path, rels: list[str]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", compresslevel=1) as t:
        for rel in rels:
            p = ws / rel
            t.add(str(p), arcname=str(p).lstrip("/"), recursive=False, filter=k8s.normalise)
    return buf.getvalue()


DELETE_IN_POD = r'''
import json, os, shutil, sys
root = sys.argv[1]
for rel in json.loads(sys.argv[2]):
    p = os.path.normpath(os.path.join(root, rel))
    if not p.startswith(root.rstrip("/") + "/"):
        continue
    if os.path.isdir(p) and not os.path.islink(p):
        shutil.rmtree(p, ignore_errors=True)
    elif os.path.lexists(p):
        os.unlink(p)
'''


# ---------------------------------------------------------------- the warm pod

class Warm:
    def __init__(self, ws: Path, skill: Path) -> None:
        self.ws, self.skill = ws, skill
        self.run = k8s.Run()
        self.synced: dict[str, tuple] = {}
        self.ready = False

    def start(self) -> None:
        why = k8s.check()
        if why:
            raise k8s.Fail(f"backend unusable: {why}")
        self.run.pick_uids()
        self.run.start_reaper()
        deadline = k8s.num("SDLC_BROKER_DEADLINE", 4 * 3600)
        spec = k8s.job_spec(self.run.job, k8s.image_ref(), [str(self.skill)], [str(self.ws)], deadline, self.run.uids)
        for meta in (spec["metadata"], spec["spec"]["template"]["metadata"]):
            meta["labels"] = dict(meta["labels"], sdlc="run", **{"sdlc-role": "broker"})
        pod = k8s.start(self.run, spec)
        k8s.barrier(self.run, pod)
        # The skill (read-only for the code) through the loader; the workspace through the runner.
        with tempfile.TemporaryDirectory(prefix="sdlc-broker-") as tmp:
            tar = Path(tmp) / "skill.tar"
            k8s.pack([str(self.skill)], [str(self.skill)], [], {}, tar)
            k8s.kubectl("exec", "-i", self.run.pod, "-c", "load", "--", "tar", "-C", "/", "-m", "-xf", "-",
                        input_bytes=tar.read_bytes(), timeout=600)
        # A Gradle daemon for this pod only (the project's gradle.properties disables daemons; the user home wins),
        # and the wrapper distributions from the image.
        setup = (f"mkdir -p {k8s.C_HOME}/.gradle/wrapper && printf 'org.gradle.daemon=true\\n' > {k8s.C_HOME}/.gradle/gradle.properties"
                 f" && cp -R /opt/gradle-dists {k8s.C_HOME}/.gradle/wrapper/dists 2>/dev/null; true")
        k8s.kubectl("exec", self.run.pod, "-c", "run", "--", "/bin/sh", "-c", setup, timeout=300)
        self.ready = True
        log(f"warm pod {self.run.pod} ready on {pod['spec'].get('nodeName')}")

    def sync(self) -> tuple[int, int]:
        now = snapshot(self.ws)
        changed, deleted = delta(self.synced, now)
        if deleted:
            k8s.kubectl("exec", self.run.pod, "-c", "run", "--", *k8s.PY, "-c", DELETE_IN_POD, str(self.ws),
                        json.dumps(deleted), timeout=300)
        if changed:
            k8s.kubectl("exec", "-i", self.run.pod, "-c", "run", "--", "tar", "-C", "/", "-m", "-xzf", "-",
                        input_bytes=pack_entries(self.ws, changed), timeout=900)
        self.synced = now
        return len(changed), len(deleted)

    def run_check(self, req: dict, out_log: Path) -> int:
        cwd = str(self.ws / req["cwd"])
        status = f"{k8s.C_TMP}/.sdlc-status-{req['id']}"
        tmo = k8s.num("SDLC_BROKER_CHECK_TIMEOUT", 1800)
        plan = {"mounts": [], "gradle_dists": False, "pi_settings": {}}
        cmd = ["exec", self.run.pod, "-c", "run", "--", "env", "-i", "-C", cwd, *k8s.env_for_pod(plan),
               f"SDLC_ENTRY_STATUS={status}", *k8s.PY, str(self.skill / "scripts" / "docker" / "entry.py"),
               "timeout", "-k", "10", str(tmo), "bash", "-c", req["cmd"]]
        with open(out_log, "ab") as fh:
            fh.write(f"$ {req['cmd']}   (warm k8s runner {self.run.pod})\n".encode())
        rc = stream_to(cmd, out_log, k8s.num("SDLC_K8S_MAX_LOG_MB", 64) * 1048576)
        done = k8s.kubectl("exec", self.run.pod, "-c", "run", "--", "head", "-c", "16", status, check=False)
        if done.returncode != 0 or done.stdout.decode().strip() != str(rc):
            raise k8s.Fail(f"the check's completion is not confirmed (exec exit {rc})")
        return rc

    def copy_back(self) -> None:
        keep = [str(self.ws / k) for k in os.environ.get("SDLC_BROKER_KEEP", KEEP_DEFAULT).split()]
        for k in keep:
            with tempfile.TemporaryDirectory(prefix="sdlc-broker-out-") as tmp:
                out = Path(tmp) / "out.tgz"
                pull = ["exec", self.run.pod, "-c", "run", "--", *k8s.PY, "-c", k8s.KEEP_IN_POD, json.dumps([k])]
                if k8s.stream_exec(pull, k8s.num("SDLC_K8S_MAX_OUTPUT_MB", 2048) * 1048576, out) != 0:
                    raise k8s.Fail(f"could not copy back {k}")
                os.makedirs(k, exist_ok=True)
                k8s.safe_extract(out, os.path.realpath(k))

    def stop(self) -> None:
        if self.run.pod or self.run.job:
            ok = self.run.cleanup()
            log("warm pod deleted" if ok else f"could not confirm that Job {self.run.job} is gone")


def stream_to(args: list[str], path: Path, cap: int) -> int:
    """kubectl exec, stdout and stderr appended to path (the role tails it), capped."""
    import subprocess  # noqa: PLC0415
    p = subprocess.Popen(["kubectl", "--context", k8s.context(), "--namespace", k8s.namespace(), *args],
                         stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    n = 0
    stdout = p.stdout
    if stdout is None:
        p.kill()
        raise k8s.Fail("kubectl exec gave no output stream")
    with open(path, "ab") as fh:
        for chunk in iter(lambda: stdout.read(8192), b""):
            n += len(chunk)
            if n > cap:
                p.kill()
                fh.write(b"\nsdlc-check: output exceeded the cap; the check was stopped\n")
                p.wait()
                return 97
            fh.write(chunk)
            fh.flush()
    return p.wait()


# ---------------------------------------------------------------- the loop

def serve(ws: Path, skill: Path) -> int:
    rem = ws / REMOTE
    for d in ("req", "res"):
        (rem / d).mkdir(parents=True, exist_ok=True)
    (rem / "ready").unlink(missing_ok=True)
    warm = Warm(ws, skill)

    def on_signal(_sig, _frame):
        global STOP
        STOP = True

    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGINT, on_signal)
    try:
        try:
            warm.start()
            (rem / "ready").write_text(warm.run.pod)
        except (k8s.Fail, SystemExit) as e:
            (rem / "unavailable").write_text(f"{e}\n")
            log(f"unavailable: {e}")
            return 1
        idle_since = time.monotonic()
        while not STOP and not (rem / "stop").exists():
            reqs = sorted((p for p in (rem / "req").glob("*.json") if not p.name.startswith(".")),
                          key=lambda p: p.stat().st_mtime)
            if not reqs:
                if time.monotonic() - idle_since > k8s.num("SDLC_BROKER_IDLE", 3 * 3600):
                    log("idle too long; stopping")
                    break
                time.sleep(0.3)
                continue
            path = reqs[0]
            try:
                req = parse_request(path, ws)
            except (ValueError, OSError) as e:
                log(f"refused request {path.name}: {e}")
                path.unlink(missing_ok=True)
                continue
            path.unlink(missing_ok=True)
            out_log = rem / "res" / f"{req['id']}.log"
            t0 = time.monotonic()
            try:
                up, gone = warm.sync()
                with open(out_log, "ab") as fh:
                    fh.write(f"sdlc-check: synced {up} changed and {gone} deleted entries\n".encode())
                rc = warm.run_check(req, out_log)
                warm.copy_back()
            except (k8s.Fail, SystemExit) as e:
                with open(out_log, "ab") as fh:
                    fh.write(f"\nsdlc-check: the runner failed: {e}\n".encode())
                rc = 97
            write_response(ws, req["id"], {"exit": rc, "seconds": round(time.monotonic() - t0, 1)})
            log(f"check {req['id']} exit {rc} in {time.monotonic() - t0:.0f}s")
            idle_since = time.monotonic()
    finally:
        (rem / "ready").unlink(missing_ok=True)
        warm.stop()
    return 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("serve")
    s.add_argument("--ws", required=True)
    s.add_argument("--role", default="")
    a = ap.parse_args(argv)
    ws = Path(os.path.realpath(a.ws))
    skill = Path(__file__).resolve().parent.parent.parent
    return serve(ws, skill)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
