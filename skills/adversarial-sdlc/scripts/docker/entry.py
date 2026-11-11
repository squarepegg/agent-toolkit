#!/usr/bin/env python3
"""In-container entrypoint for the Docker sandbox backend (spec docker-backend.md, A-2 and A-3).

run.py starts every container as `python3 <skill>/scripts/docker/entry.py COMMAND [ARGS]`, with a
JSON plan in SDLC_DOCKER_ENTRY. This script runs from the read-only skill mount. It:

1. checks it is inside an sdlc image (the marker file in the read-only root filesystem);
2. checks every bind mount is the host directory, not an empty VM stand-in (A-3). A --rw dir must
   contain the host-written nonce file, which is removed here. A --ro dir must list the same
   top-level entries as on the host;
3. prepares private state on tmpfs: the Gradle wrapper distribution (A-2) and pi settings;
4. execs COMMAND.

Exit 97 (sandbox unavailable) if a check fails; COMMAND never runs.
"""
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from typing import NoReturn

MARKER = "/etc/sdlc-container"


def die(msg: str) -> NoReturn:
    print(f"sandbox.sh (docker): {msg}", file=sys.stderr, flush=True)
    sys.exit(97)


def listing_sha(path: str) -> str:
    if os.path.isdir(path):
        names = sorted(os.listdir(path))
        return hashlib.sha256("\n".join(names).encode("utf-8", "surrogateescape")).hexdigest()
    return f"file:{os.path.getsize(path)}"


def main() -> None:
    try:
        with open(MARKER, encoding="utf-8") as f:
            if f.read().strip() != "sdlc-docker-backend":
                die("marker mismatch: not an sdlc image")
    except OSError:
        die("not running in an sdlc image (no marker)")
    try:
        plan = json.loads(os.environ.pop("SDLC_DOCKER_ENTRY"))
    except (KeyError, ValueError):
        die("no entry plan")
    if len(sys.argv) < 2:
        die("no command")

    for m in plan["mounts"]:
        path = m["path"]
        if m["mode"] == "rw":
            probe = os.path.join(path, m["nonce"])
            if not os.path.isfile(probe):
                die(f"mount not shared with the Docker VM: {path} (host nonce not visible; share this path "
                    "with the Docker VM, or set TMPDIR to a shared directory)")
            try:
                os.unlink(probe)
            except OSError as e:
                die(f"rw mount not writable: {path}: {e}")
        else:
            try:
                got = listing_sha(path)
            except OSError as e:
                die(f"ro mount unreadable: {path}: {e}")
            if got != m["listing"]:
                die(f"mount not shared with the Docker VM: {path} (contents differ from the host)")

    home = os.environ["HOME"]
    if plan.get("gradle_dists"):
        dst = os.path.join(os.environ["GRADLE_USER_HOME"], "wrapper", "dists")
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copytree("/opt/gradle-dists", dst, symlinks=True, dirs_exist_ok=True)
    agent = os.environ.get("PI_CODING_AGENT_DIR")
    if agent:
        os.makedirs(agent, exist_ok=True)
        with open(os.path.join(agent, "settings.json"), "w", encoding="utf-8") as f:
            json.dump(plan.get("pi_settings") or {}, f)
    del home  # HOME, TMPDIR and GRADLE_USER_HOME are tmpfs mounts that run.py set up
    # k8s backend: tell the host that the checks passed and the command is about to start (a missing marker
    # afterwards means a setup failure, so no outputs are copied back).
    fsize = os.environ.pop("SDLC_ENTRY_FSIZE_MB", "")
    if fsize:
        # k8s backend: no file may grow beyond this (bounds what Kubernetes keeps on the node disk).
        import resource  # noqa: PLC0415 - Linux only, needed only here

        limit = int(fsize) * 1024 * 1024
        resource.setrlimit(resource.RLIMIT_FSIZE, (limit, limit))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))  # no core dumps (they would land on the node)
    marker = os.environ.pop("SDLC_ENTRY_OK", "")
    if marker:
        with open(marker, "w", encoding="utf-8") as f:
            f.write("ok\n")
    status = os.environ.pop("SDLC_ENTRY_STATUS", "")
    try:
        if status:
            # k8s backend: the command's exit status is written to a file, so the host can tell a completed
            # command from a broken exec connection (kubectl reports both through its own exit code).
            child = subprocess.Popen(sys.argv[1:])  # noqa: S603 - argv exactly as the caller gave it
            rc = child.wait()
            rc = 128 - rc if rc < 0 else rc
            # A fresh, exclusively created file (no name the workload could have prepared as a link).
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(status), prefix=".sdlc-status-")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(f"{rc}\n")
            os.replace(tmp, status)
            sys.exit(rc)
        # No shell on purpose: argv is passed through exactly as the caller gave it to sandbox.sh.
        os.execvp(sys.argv[1], sys.argv[1:])  # noqa: S606
    except OSError as e:
        if marker:  # the command never started: a setup failure (97 on the host), not a result
            os.unlink(marker)
        print(f"sandbox.sh (docker): cannot run {sys.argv[1]}: {e}", file=sys.stderr, flush=True)
        sys.exit(127 if isinstance(e, FileNotFoundError) else 126)


if __name__ == "__main__":
    main()
