#!/usr/bin/env python3
"""Helpers for the adversarial-sdlc skill. Stdlib only, Python 3.9+.

gate.sh and sdlc.sh call these. Verdicts come from files on disk, never from a model.
Configuration comes from the environment (gate.config / sdlc.config, exported with set -a).
Every command fails closed: on an unreadable or malformed input it exits non-zero.

File sets: a project's files are everything under its root, except
  - .git anywhere, and .sdlc / .sdlc-in / .sdlc-sbx / .tmp at the root;
  - the exact relative paths in GATE_GENERATED_PATHS (build output), and nothing else;
  - for the lock: GATE_MAIN_PATHS and GATE_DOC_PATHS; for the fingerprint: GATE_DOC_PATHS.
Symlinks are recorded by their target, never followed.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import re
import shlex
import shutil
import signal
import stat
import struct
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

SCRIPTS = Path(__file__).resolve().parent
ROOT_PRIVATE = (".sdlc", ".sdlc-in", ".sdlc-sbx", ".tmp")   # run state, role inputs, sandbox state
ID_RX = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
SPEC_ID = re.compile(r"\b(INV|FM|AC|NG|ESC)-\d+\b")
MANDATORY_STEPS = ("integrity", "typecheck", "test", "mutation", "static")
MODES = ("required", "optional", "off")
KINDS = ("example", "property", "crash-retry-replay")


def env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


def env_list(name: str, default: str = "") -> list[str]:
    return [p.strip("/") for p in env(name, default).split() if p.strip("/")]


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def dump(path: Path, data) -> None:
    """Atomic JSON write."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    os.replace(tmp, path)


def parse_json(text: str, what: str):
    """json.loads that names what it was parsing (a ValueError, so callers that catch ValueError still do)."""
    try:
        return json.loads(text)
    except ValueError as e:
        raise ValueError(f"{what}: invalid JSON ({e})") from e


def load(path: Path):
    return parse_json(Path(path).read_text(), str(path))


def read_jsonl(path: Path) -> list:
    """Every non-blank line of a JSON-lines file; [] if the file is absent; a malformed line is an error."""
    if not path.exists():
        return []
    return [parse_json(ln, f"{path}:{i}") for i, ln in enumerate(path.read_text().splitlines(), 1) if ln.strip()]


# ---------------------------------------------------------------- file sets

def _excluded(rel: str, excludes: list[str]) -> bool:
    return any(rel == e or rel.startswith(e + "/") for e in excludes)


def iter_files(root: Path, include: list[str] | None = None, exclude: list[str] | None = None):
    """Yield (relative path, Path) for files and symlinks, sorted, per the module rules."""
    root = Path(root)
    excludes = exclude or []
    for inc in include or ["."]:
        start = root / inc if inc != "." else root
        if start.is_symlink() or start.is_file():
            rel = str(start.relative_to(root))
            if not _excluded(rel, excludes):
                yield rel, start
            continue
        if not start.is_dir():
            continue
        generated = set(env_list("GATE_GENERATED_PATHS", "build .gradle .kotlin buildSrc/build buildSrc/.gradle"))
        for dirpath, dirnames, filenames in os.walk(start):
            d = Path(dirpath)
            keep = []
            for name in sorted(dirnames):
                rel = str((d / name).relative_to(root))
                if name == ".git" or rel in ROOT_PRIVATE or rel in generated:
                    continue
                if _excluded(rel, excludes):
                    continue
                if (d / name).is_symlink():
                    filenames.append(name)
                    continue
                keep.append(name)
            dirnames[:] = keep
            for f in sorted(filenames):
                if f == ".DS_Store":
                    continue
                rel = str((d / f).relative_to(root))
                if not _excluded(rel, excludes):
                    yield rel, d / f


def digest(p: Path) -> str:
    if p.is_symlink():
        return hashlib.sha256(("link:" + os.readlink(p)).encode()).hexdigest()
    # The owner-exec bit, not access(2): Docker Desktop's bind mounts report every file as executable.
    mode = "x" if p.stat().st_mode & stat.S_IXUSR else "-"
    return hashlib.sha256(mode.encode() + b"\0" + p.read_bytes()).hexdigest()


def contained(root: Path, rel: str, within: list[str]) -> Path:
    """Resolve rel under root; it must stay inside one of the `within` subtrees and not be a symlink."""
    if not rel or os.path.isabs(rel) or ".." in Path(rel).parts:
        raise ValueError(f"unsafe path {rel!r}")
    cur = Path(root)
    for part in Path(rel).parts:
        cur = cur / part
        if cur.is_symlink():
            raise ValueError(f"path {rel!r} goes through a symlink ({cur.name})")
    rp = (Path(root) / rel).resolve()
    if not any(rp == (Path(root) / w).resolve() or (Path(root) / w).resolve() in rp.parents for w in within):
        raise ValueError(f"path {rel!r} is not inside {within}")
    return rp


def safe_read(root: Path, rel: str) -> bytes:
    """Read a regular file a role wrote, refusing symlinks anywhere on the path (no-follow open)."""
    contained(Path(root), rel, ["."])
    fd = os.open(Path(root) / rel, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"{rel!r} is not a regular file")
        chunks = []
        while True:
            b = os.read(fd, 1 << 20)
            if not b:
                break
            chunks.append(b)
        return b"".join(chunks)
    finally:
        os.close(fd)


def safe_write(dst: Path, data: bytes, mode: int = 0o644) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.is_symlink() or dst.exists():
        dst.unlink()
    fd = os.open(dst, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), mode)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


def cmd_import(a):
    """Copy files a role wrote (REL paths under WS) to DST, refusing symlinks and non-regular files."""
    ws, dst = Path(a.ws).resolve(), Path(a.dst)
    done, refused = [], []
    for rel in a.files:
        try:
            src = ws / rel
            mode = 0o755 if (src.exists() and not src.is_symlink() and os.access(src, os.X_OK)) else 0o644
            safe_write(dst / (rel if not a.flat else Path(rel).name), safe_read(ws, rel), mode)
            done.append(rel)
        except (OSError, ValueError) as e:
            refused.append(f"{rel}: {e}")
    print(json.dumps({"imported": len(done), "refused": refused}))
    sys.exit(0 if not refused else 1)


def cmd_import_tree(a):
    """Copy every regular file under WS/REL to DST/REL; symlinks and special files are skipped and listed."""
    ws, dst = Path(a.ws).resolve(), Path(a.dst)
    done, skipped = 0, []
    base = ws / a.rel
    if base.is_symlink():
        print(json.dumps({"imported": 0, "skipped": [a.rel]}))
        sys.exit(1)
    for dirpath, dirnames, filenames in os.walk(base) if base.is_dir() else []:
        d = Path(dirpath)
        for n in list(dirnames):
            if (d / n).is_symlink():
                skipped.append(str((d / n).relative_to(ws)))
                dirnames.remove(n)
        for f in filenames:
            rel = str((d / f).relative_to(ws))
            try:
                safe_write(dst / rel, safe_read(ws, rel))
                done += 1
            except (OSError, ValueError):
                skipped.append(rel)
    print(json.dumps({"imported": done, "skipped": skipped}))


SANDBOXED_KINDS = ("sandbox-exec", "docker", "inherited")  # sandbox.sh --probe values that confine
_IMAGE_IDS: dict[str, str] | None = None


def docker_image_ids() -> dict[str, str]:
    """Pinned sdlc image IDs (INV-8): {} unless the docker backend can be selected. Inside a container,
    run.py passes them as SDLC_DOCKER_ID_*; on the host, scripts/docker/run.py --ids resolves them."""
    global _IMAGE_IDS
    if _IMAGE_IDS is None:
        ids = {k[len("SDLC_DOCKER_ID_"):].lower(): v for k, v in os.environ.items() if k.startswith("SDLC_DOCKER_ID_")}
        if not ids and env("SDLC_SANDBOX_BACKEND", "auto") != "sandbox-exec":
            p = subprocess.run([sys.executable, str(SCRIPTS / "docker" / "run.py"), "--ids"],
                               capture_output=True, text=True, check=False)
            for line in p.stdout.splitlines() if p.returncode == 0 else []:
                k, _, v = line.partition("=")
                ids[k[len("SDLC_DOCKER_ID_"):].lower()] = v
        _IMAGE_IDS = ids
    return dict(_IMAGE_IDS)


def harness_sha() -> str:
    h = hashlib.sha256()
    base = SCRIPTS.parent
    for sub in ("scripts", "roles", "templates"):
        for f in sorted((base / sub).rglob("*")):
            rel_parts = f.relative_to(base).parts
            if (f.is_file() and not f.is_symlink() and "__pycache__" not in rel_parts
                    and not any(x.startswith(".") for x in rel_parts)):
                h.update(str(f.relative_to(base)).encode() + b"\0" + f.read_bytes())
    # The container images are part of the harness: rebuilding one invalidates harness-bound records.
    for k, v in sorted(docker_image_ids().items()):
        h.update(f"docker-image\0{k}\0{v}".encode())
    # Code under test on Kubernetes: the pinned runner image is part of the harness too.
    k8s = k8s_code_backend()
    if k8s:
        h.update(f"k8s-image\0{k8s['image']}".encode())
    return h.hexdigest()


def k8s_code_backend() -> dict | None:
    """Where code under test runs when SDLC_SANDBOX_BACKEND_CODE=k8s (scripts/k8s/run.py), else None."""
    if os.environ.get("SDLC_SANDBOX_BACKEND_CODE") != "k8s":
        return None
    p = subprocess.run([sys.executable, str(SCRIPTS / "k8s" / "run.py"), "--ids"], capture_output=True, text=True,
                       check=False)
    image = p.stdout.strip().split("=", 1)[1] if p.returncode == 0 and "=" in p.stdout else "unpinned"
    return {"backend": "k8s", "context": os.environ.get("SDLC_K8S_CONTEXT", ""),
            "namespace": os.environ.get("SDLC_K8S_NAMESPACE", "sdlc-runner"), "image": image}


def sandbox_info() -> dict:
    """The sandbox backend this process would use, plus pinned image IDs on Docker (INV-8)."""
    p = subprocess.run([str(SCRIPTS / "sandbox.sh"), "--probe"], capture_output=True, text=True, check=False)
    backend = p.stdout.strip() or "none"
    info: dict = {"backend": backend}
    if backend in ("docker", "inherited") and docker_image_ids():
        info["images"] = docker_image_ids()
    k8s = k8s_code_backend()
    if k8s:
        info["code"] = k8s  # gate steps and repros run here; roles use the backend above
    return info


def cmd_sandbox_info(a):
    del a
    print(json.dumps(sandbox_info()))


def cmd_harness_sha(a):
    del a
    print(harness_sha())


def sha_file(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# Orchestrator settings (models, retries, sandbox modes): roles cannot reach them, and every setting that
# changes a result is recorded in the role ledger and in attack/calibration identities. They are not
# gate inputs, so they are neither locked nor fingerprinted.
ORCHESTRATOR_FILES = ["sdlc.config"]


def fp_excludes() -> list[str]:
    return env_list("GATE_DOC_PATHS") + ORCHESTRATOR_FILES


def lock_excludes() -> list[str]:
    return env_list("GATE_MAIN_PATHS", "src/main") + env_list("GATE_DOC_PATHS") + ORCHESTRATOR_FILES


def fingerprint(root: Path) -> str:
    h = hashlib.sha256()
    for rel, p in iter_files(root, exclude=fp_excludes()):
        h.update(f"{rel}\0{digest(p)}\n".encode())
    return h.hexdigest()


def cmd_fingerprint(a):
    print(fingerprint(Path(a.root)))


def cmd_copy(a):
    """Copy the project's file set (or only PATHS) into DST. Unsafe symlinks are skipped."""
    src, dst = Path(a.src).resolve(), Path(a.dst)
    n = 0
    for rel, p in iter_files(src, include=a.paths or None, exclude=fp_excludes() if a.no_docs else []):
        out = dst / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if p.is_symlink():
            target = (p.parent / os.readlink(p)).resolve()
            if src not in target.parents:
                print(f"copy: skipped unsafe symlink {rel}", file=sys.stderr)
                continue
            if out.exists() or out.is_symlink():
                out.unlink()
            os.symlink(os.readlink(p), out)
        else:
            shutil.copy2(p, out)
        n += 1
    print(json.dumps({"copied": n}))


def cmd_lock(a):
    root, lock = Path(a.root), Path(a.lock)
    if a.add:
        data = load(lock)
        for rel in a.add:
            p = root / rel
            if p.exists() or p.is_symlink():
                data["files"][rel] = digest(p)
            else:
                data["files"].pop(rel, None)
    else:
        data = {"excludes": lock_excludes(), "spec_sha": a.spec_sha,
                "files": {rel: digest(p) for rel, p in iter_files(root, exclude=lock_excludes())}}
    data["updated"] = now()
    before = sha_file(lock) if lock.exists() else ""
    dump(lock, data)
    entry = {"at": now(), "before": before, "after": sha_file(lock),
             "reason": a.reason or ("add" if a.add else "lock"), "added": a.add or []}
    try:
        with open(lock.parent / "lock-history.jsonl", "a") as h:
            h.write(json.dumps(entry) + "\n")
    except OSError as e:  # a lock change with no history entry is an audit gap: fail
        raise OSError(f"cannot record the lock change in {lock.parent / 'lock-history.jsonl'}: {e}") from e
    print(json.dumps({"lock": str(lock), "files": len(data["files"])}))


def lock_history(run: Path) -> list[dict]:
    return read_jsonl(run / "lock-history.jsonl")


def verify_lock(root: Path, lock: Path) -> dict:
    data = load(lock)
    current = {rel: digest(p) for rel, p in iter_files(root, exclude=data["excludes"])}
    changed = sorted(r for r, d in data["files"].items() if r in current and current[r] != d)
    missing = sorted(r for r in data["files"] if r not in current)
    added = sorted(r for r in current if r not in data["files"])
    return {"ok": not (changed or missing or added), "changed": changed, "missing": missing,
            "added": added, "locked_files": len(data["files"]), "spec_sha": data.get("spec_sha", "")}


def cmd_verify_lock(a):
    r = verify_lock(Path(a.root), Path(a.lock))
    print(json.dumps(r, indent=2))
    sys.exit(0 if r["ok"] else 1)


def cmd_ws_list(a):
    """Record every file in a workspace with its digest (to detect what a role wrote)."""
    dump(Path(a.out), {rel: digest(p) for rel, p in iter_files(Path(a.ws))})


def cmd_ws_changes(a):
    before = load(Path(a.before))
    after = {rel: digest(p) for rel, p in iter_files(Path(a.ws)) if not rel.startswith(".tmp/")}
    print(json.dumps({"new": sorted(r for r in after if r not in before),
                      "modified": sorted(r for r in after if r in before and before[r] != after[r]),
                      "deleted": sorted(r for r in before if r not in after)}))


# ---------------------------------------------------------------- sandboxed command runner

def sandboxed(cmd: str, rw: list[Path], deny: list[Path]) -> list[str]:
    """Sandbox for running code under test in a disposable copy: the copy is read-only except its
    generated-output paths, the network is loopback only."""
    args = [str(SCRIPTS / "sandbox.sh"), "--net", "local"]
    for d in rw:
        args += ["--ro", str(d)]
        for g in env_list("GATE_GENERATED_PATHS", "build .gradle .kotlin"):
            (Path(d) / g).mkdir(parents=True, exist_ok=True)
            args += ["--rw", str(Path(d) / g)]
    for d in deny:
        args += ["--deny", str(d)]
    return [*args, "--", "bash", "-c", cmd]


def run_group(argv: list[str], cwd: Path | None, timeout: int, capture: bool):
    """Run in a new process group; on timeout, and after exit, kill the whole group (no survivors)."""
    p = subprocess.Popen(argv, cwd=cwd, start_new_session=True, text=True,
                         stdout=subprocess.PIPE if capture else None, stderr=subprocess.STDOUT if capture else None)
    try:
        out, _ = p.communicate(timeout=timeout)
        code = p.returncode
    except subprocess.TimeoutExpired:
        kill_group(p.pid, signal.SIGTERM)
        time.sleep(2)
        kill_group(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        code = 124
    kill_group(p.pid, signal.SIGKILL)   # no survivors, even after a normal exit
    return code, out or ""


def kill_group(pgid: int, sig: int) -> None:
    try:
        os.killpg(pgid, sig)
    except ProcessLookupError:
        return
    except PermissionError:
        # macOS refuses killpg while the group leader is a zombie; signal the members one by one.
        subprocess.run(["pkill", f"-{int(sig)}", "-g", str(pgid)], check=False, capture_output=True)


def run_cmd(argv: list[str], cwd: Path, timeout: int, log: Path) -> int:
    start = time.time()
    code, out = run_group(argv, cwd, timeout, capture=True)
    if code == 124:
        out = f"TIMEOUT after {timeout}s (process group killed)\n{out}"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(f"$ {' '.join(argv[-1:])}\nexit={code} seconds={int(time.time() - start)}\n\n{out}")
    return code


def cmd_run_timeout(a):
    """run-timeout SECS -- CMD...: portable timeout (exit 124 on timeout)."""
    code, _ = run_group(a.cmd, None, a.secs, capture=False)
    if code == 124:
        print(f"run-timeout: process group killed after {a.secs}s", file=sys.stderr)
    sys.exit(code)


# ---------------------------------------------------------------- test sources and reports

PKG = re.compile(r"^\s*package\s+([\w.]+)", re.MULTILINE)
CLS = re.compile(r"^(?:(?:internal|public|open|abstract|data)\s+)*class\s+(\w+)", re.MULTILINE)


def test_classes(path: Path) -> list[str]:
    try:
        text = path.read_text(errors="replace")
    except OSError:
        return []
    if not (re.search(r"\w+Spec\s*\(", text) or "@Test" in text or re.search(r":\s*\w+Spec\b", text)):
        return []
    pkg = PKG.search(text)
    prefix = (pkg.group(1) + ".") if pkg else ""
    return [prefix + m.group(1) for m in CLS.finditer(text)]


def parse_report_xml(path: Path) -> ET.Element:
    """Parse a report written by code under test. Such reports are adversarial input, read outside the sandbox:
    DTDs and entity declarations are refused (the defusedxml rule, stdlib only). JUnit, Pitest and checkstyle
    XML never contain them. Reports must be UTF-8 (no NUL bytes): another encoding, such as UTF-16, would hide a
    declaration from the byte check below. Size is capped (SDLC_MAX_REPORT_MB, default 256) before reading.
    ValueError makes the gate step fail with a reason."""
    name = Path(path).name
    cap = int(os.environ.get("SDLC_MAX_REPORT_MB", "256")) * 1024 * 1024
    if Path(path).stat().st_size > cap:
        raise ValueError(f"{name}: report larger than {cap // 1048576} MB")
    data = Path(path).read_bytes()
    if b"\0" in data:
        raise ValueError(f"{name}: NUL bytes in a report (only UTF-8 reports are accepted)")
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as e:
        raise ValueError(f"{name}: report is not UTF-8 ({e.reason} at byte {e.start})") from e
    decl = re.match(r"\ufeff?\s*<\?xml\b([^>]*)\?>", text)
    enc = re.search(r"\bencoding\s*=\s*[\"']([^\"']*)[\"']", decl.group(1)) if decl else None
    if enc and enc.group(1).lower().replace("-", "") != "utf8":
        raise ValueError(f"{name}: report declares an encoding other than UTF-8")
    if re.search(rb"<!\s*(DOCTYPE|ENTITY)", data, re.IGNORECASE):
        raise ValueError(f"{name}: DTD or entity declarations are not allowed in reports")
    try:
        # Parsed as text: the parser cannot re-interpret the bytes under a declared encoding.
        return ET.fromstring(text)  # noqa: S314 -- UTF-8 only, DTDs and entities refused above
    except ET.ParseError as e:
        raise ValueError(f"{name}: malformed report: {e}") from e


def parse_junit(directory: Path, since: float = 0.0) -> dict:
    cases = []
    stale = 0
    for x in sorted(Path(directory).rglob("*.xml")) if Path(directory).is_dir() else []:
        if x.stat().st_mtime + 1 < since:
            stale += 1
            continue
        root = parse_report_xml(x)
        for s in ([root] if root.tag == "testsuite" else list(root.iter("testsuite"))):
            for tc in s.iter("testcase"):
                status, msg = "passed", ""
                ftype, trace = "", ""
                for tag in ("failure", "error"):
                    el = tc.find(tag)
                    if el is not None:
                        status = "failed"
                        lines = (el.get("message") or el.text or "").strip().splitlines()
                        msg = lines[0][:300] if lines else ""
                        ftype, trace = el.get("type", ""), (el.text or "")[:6000]
                if tc.find("skipped") is not None:
                    status = "skipped"
                cases.append({"class": tc.get("classname", s.get("name", "")), "name": tc.get("name", ""),
                              "status": status, "message": msg, "type": ftype, "trace": trace})
    return {"tests": len(cases), "stale_files": stale, "cases": cases,
            "failed": [c for c in cases if c["status"] == "failed"],
            "skipped": [c for c in cases if c["status"] == "skipped"]}


def case_matches(case: dict, cls: str, name: str) -> bool:
    if not (case["class"] == cls or case["class"].startswith((cls + ".", cls + "$"))):
        return False
    return case["name"] == name or case["name"].endswith(name)


def parse_pitest(xml: Path) -> dict:
    root = parse_report_xml(xml)
    counts: dict[str, int] = {}
    survived = []
    for m in root.iter("mutation"):
        st = m.get("status", "UNKNOWN")
        counts[st] = counts.get(st, 0) + 1
        if st in ("SURVIVED", "NO_COVERAGE"):
            survived.append({"class": m.findtext("mutatedClass"), "method": m.findtext("mutatedMethod"),
                             "line": m.findtext("lineNumber"), "status": st,
                             "mutator": (m.findtext("mutator") or "").rsplit(".", 1)[-1],
                             "description": m.findtext("description")})
    total = sum(counts.values())
    killed = counts.get("KILLED", 0) + counts.get("TIMED_OUT", 0)
    errors = sum(counts.get(s, 0) for s in ("RUN_ERROR", "MEMORY_ERROR", "NON_VIABLE", "UNKNOWN"))
    return {"total": total, "killed": killed, "counts": counts, "errors": errors, "survived": survived,
            "score_exact": f"{killed}/{total}", "score": (100.0 * killed / total) if total else 0.0}


# Sharded mutation (speed spec, amendment A2). Shards partition the mutation targets by top-level class (C and its
# nested classes C$*). The class list and the weights come from the previous gate's kept report. The lightest shard is
# also the remainder: it targets its own classes and every target class no other shard has (the full targets minus
# the other shards' classes), so a class added since then is still mutated, and every shard has mutants to report
# (Pitest writes no report when it finds none). merge_mutations() verifies the partition instead of trusting it.

JAVA_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)*$")


def top_class(name: str) -> str:
    return name.split("$", 1)[0]


def mutant_key(m: ET.Element) -> tuple:
    """A mutant's identity, as Pitest's: class, method and descriptor, line, mutator, and every instruction index
    and block (several mutants of one mutator can sit on one line, told apart only by their indexes)."""
    multi = lambda tag: ",".join((e.text or "").strip() for e in m.findall(tag))  # noqa: E731
    return (*(m.findtext(t) or "" for t in ("mutatedClass", "mutatedMethod", "methodDescription", "lineNumber",
                                            "mutator")),
            multi("indexes/index") or (m.findtext("index") or ""), multi("blocks/block") or (m.findtext("block") or ""))


def read_maybe_gz(path: Path) -> Path:
    """A kept report is gzipped (evidence/mutations.xml.gz): decompress to a temp file, size-capped."""
    if not str(path).endswith(".gz"):
        return path
    import gzip  # noqa: PLC0415 - only needed here
    cap = int(os.environ.get("SDLC_MAX_REPORT_MB", "256")) * 1024 * 1024
    with gzip.open(path, "rb") as f:
        data = f.read(cap + 1)
    if len(data) > cap:
        raise ValueError(f"{path.name}: report larger than {cap // 1048576} MB")
    out = Path(tempfile.mkdtemp(prefix="sdlc-mx-")) / "mutations.xml"
    out.write_bytes(data)
    return out


def class_costs(previous: Path, plan: Path | None, result: Path | None) -> dict[str, float]:
    """Thread-seconds of mutation work per class, from the previous sharded gate: each shard's measured Pitest time
    after coverage, times its threads, shared out by its classes' numberOfTestsRun. The inputs come from code under
    test's run (its report and log lines): they steer the schedule only, never what runs or how it is judged."""
    if plan is None or result is None or not plan.exists() or not result.exists():
        return {}
    try:  # a missing or corrupt previous plan only costs the schedule its hints
        shards = load(plan)
        res = {r["id"]: r for r in load(result)}
    except (OSError, ValueError, KeyError, TypeError):
        return {}
    tests: dict[str, float] = {}
    for m in parse_report_xml(read_maybe_gz(previous)).iter("mutation"):
        c = top_class(m.findtext("mutatedClass") or "")
        if JAVA_NAME.match(c):
            tests[c] = tests.get(c, 0.0) + max(1, int(m.get("numberOfTestsRun") or 1))
    costs: dict[str, float] = {}
    for s in shards:
        r = res.get(s["id"])
        if not r or r.get("exit") != 0 or not r.get("pitest_seconds") or not s.get("classes"):
            continue
        work = max(1.0, (r["pitest_seconds"] - (r.get("coverage_seconds") or 0)) * max(1, r.get("threads") or 1))
        mine = [c for c in s["classes"] if c in tests]
        total = sum(tests[c] for c in mine) or 1.0
        for c in mine:
            costs[c] = work * tests[c] / total
    return costs


# CPUs per Pitest thread in a sized shard. Each minion is its own JVM with JIT and GC threads; oversubscribed minions
# hit resource limits that the tests count as failures, i.e. spurious kills. Measured on a Kotlin parser project (2026-11-05,
# full strength): 14 threads on 15 CPUs flipped a mutant to KILLED in 2 of 3 runs; 14 on 29 and 5 on 6
# gave the reference result every time.
CPU_PER_THREAD = 2


def size_shards(bins: list[dict], counts: dict[str, int], unit_size: int, max_threads: int, cpu_budget: int) -> None:
    """Threads per shard so that the shards finish together, within the CPU budget (CPU_PER_THREAD per thread plus 1
    per shard): the smallest common target time t with sum(CPU_PER_THREAD * ceil(cost / t) + 1) <= budget. A shard
    never gets more threads than it has analysis units (more would idle)."""
    units = [max(1, sum(-(-counts.get(c, 1) // max(1, unit_size)) for c in b["classes"])) for b in bins]
    caps = [min(max_threads, u) for u in units]
    lo = max(b["cost"] / cap for b, cap in zip(bins, caps, strict=True))
    t = max(lo, 1.0)
    threads = [1] * len(bins)
    for _ in range(200):
        threads = [min(cap, max(1, -(-int(b["cost"]) // max(1, int(t))))) for b, cap in zip(bins, caps, strict=True)]
        if sum(CPU_PER_THREAD * x + 1 for x in threads) <= cpu_budget:
            break
        t *= 1.1
    for b, th, u in zip(bins, threads, units, strict=True):
        b["threads"] = th
        b["cpu"] = CPU_PER_THREAD * th + 1
        # Smaller analysis units where a shard has few units per thread: about two units per thread, so a few slow
        # units no longer leave the other threads idle (parser project: 14 units on 14 threads took 178 s, as on 8).
        mutants = sum(counts.get(c, 0) for c in b["classes"])
        if u < 2 * th and mutants:
            b["unit_size"] = max(1, -(-mutants // (2 * th)))


def plan_shards(previous: Path | None, max_shards: int, per_shard: int, costs: dict[str, float] | None = None,
                unit_size: int = 10, max_threads: int = 15, cpu_budget: int = 0) -> list[dict]:
    """Shards: [{"id", "classes": [top-level classes] (None: unsharded), "weight", "remainder"}], plus "threads" and
    "cpu" when measured costs are known (cost-balanced and sized). One unsharded run without a previous report or
    when one shard suffices."""
    weights: dict[str, int] = {}
    if previous is not None and previous.exists():
        for m in parse_report_xml(read_maybe_gz(previous)).iter("mutation"):
            c = top_class(m.findtext("mutatedClass") or "")
            if JAVA_NAME.match(c):  # the report is untrusted: anything else is left to the remainder
                weights[c] = weights.get(c, 0) + 1
    total = sum(weights.values())
    n = min(max_shards, len(weights), -(-total // max(1, per_shard)))
    if n < 2:
        return [{"id": 0, "classes": None, "weight": total, "remainder": True}]
    costs = costs or {}
    sized = bool(costs) and cpu_budget > 0
    if sized:
        # Classes the measurements miss (new since then) cost as much per mutant as the median measured class.
        per = sorted(costs[c] / weights[c] for c in costs if c in weights) or [1.0]
        cost = {c: costs.get(c, per[len(per) // 2] * w) for c, w in weights.items()}
    else:
        cost = {c: float(w) for c, w in weights.items()}
    bins: list[dict] = [{"id": i, "classes": [], "weight": 0, "cost": 0.0, "remainder": False} for i in range(n)]
    for c in sorted(cost, key=lambda k: (-cost[k], k)):  # longest processing time first
        b = min(bins, key=lambda x: (x["cost"], x["id"]))
        b["classes"].append(c)
        b["weight"] += weights[c]
        b["cost"] += cost[c]
    for b in bins:
        b["classes"].sort()
    min(bins, key=lambda x: (x["cost"], -x["id"]))["remainder"] = True
    if sized:
        size_shards(bins, weights, unit_size, max_threads, cpu_budget)
    return bins


def shard_args(shards: list[dict], template: str, base_targets: str) -> list[str]:
    """Each shard's extra build arguments, from the stack's GATE_MUTATION_SHARD_ARGS template, shell-quoted
    ({targets}, {excluded}: comma-separated Pitest globs; class names contain '$')."""
    out = []
    for s in shards:
        if s["classes"] and not s.get("remainder"):
            targets, excluded = ",".join(g for c in s["classes"] for g in (c, c + "$*")), ""
        else:
            others = [c for x in shards if x is not s and x["classes"] for c in x["classes"]]
            targets, excluded = ",".join(base_targets.split()), ",".join(g for c in others for g in (c, c + "$*"))
        out.append(template.replace("{targets}", shlex.quote(targets)).replace("{excluded}", shlex.quote(excluded))
                   .replace("{fail_when_empty}", "false" if len(shards) > 1 else "true"))
    return out


def merge_mutations(shards: list[dict], reports: list[Path | None], out: Path) -> dict:
    """Merge shard reports into one Pitest XML. Raises ValueError unless the reports form an exact partition:
    every shard reported, each mutant belongs to its shard's classes (the remainder: to none of the assigned
    classes), and no mutant appears twice."""
    if len(reports) != len(shards):
        raise ValueError(f"{len(shards)} shards but {len(reports)} reports")
    assigned = {c: s["id"] for s in shards if s["classes"] and not s.get("remainder") for c in s["classes"]}
    seen: dict[tuple, int] = {}
    merged = ET.Element("mutations")
    per_shard = []
    for s, rp in zip(shards, reports, strict=True):
        if rp is None or not rp.exists():
            raise ValueError(f"shard {s['id']}: no report")
        n = 0
        for m in parse_report_xml(rp).iter("mutation"):
            c = top_class(m.findtext("mutatedClass") or "")
            if not s.get("remainder") and c not in (s["classes"] or []):
                raise ValueError(f"shard {s['id']}: mutant of {c}, which is not in this shard")
            if s.get("remainder") and c in assigned:
                raise ValueError(f"remainder shard {s['id']}: mutant of {c}, which belongs to shard {assigned[c]}")
            k = mutant_key(m)
            if k in seen:
                raise ValueError(f"shard {s['id']}: mutant {k[0]}.{k[1]} line {k[3]} also in shard {seen[k]}")
            seen[k] = s["id"]
            merged.append(m)
            n += 1
        per_shard.append(n)
    out.parent.mkdir(parents=True, exist_ok=True)
    ET.ElementTree(merged).write(out, encoding="utf-8", xml_declaration=True)
    return {"shards": len(shards), "mutants": len(seen), "per_shard": per_shard}


def cmd_mutation_shards(a):
    prev = Path(a.previous) if a.previous else None
    costs = {}
    if prev is not None and a.cpu_budget > 0:
        gate_dir = prev.parent.parent  # <gate>/evidence/mutations.xml.gz
        try:
            costs = class_costs(prev, gate_dir / "mutation-shards.json", gate_dir / "mutation-shards.result.json")
        except (ValueError, OSError, KeyError, TypeError) as e:
            print(f"mutation-shards: previous timings unusable ({e}); balancing by mutant counts", file=sys.stderr)
    shards = plan_shards(prev, a.max, a.per_shard, costs, a.unit_size, a.max_threads, a.cpu_budget)
    args = shard_args(shards, a.template, a.targets)
    json.dump([dict(s, args=x) for s, x in zip(shards, args, strict=True)], sys.stdout)


def cmd_shard_times(a):
    """Per-shard Pitest timings from the shard logs (Pitest's own "Calculated coverage in" / "Completed in" lines)."""
    shards = load(Path(a.plan))
    out = []
    for s, log, code in zip(shards, a.logs, a.exits, strict=True):
        text = Path(log).read_text(errors="replace") if Path(log).exists() else ""
        cov = re.findall(r"Calculated coverage in (\d+) seconds", text)
        done = re.findall(r"Completed in (\d+) seconds", text)
        out.append({"id": s["id"], "exit": int(code), "threads": s.get("threads") or a.default_threads,
                    "coverage_seconds": int(cov[-1]) if cov else None, "pitest_seconds": int(done[-1]) if done else None})
    try:
        with open(a.out, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=1)
    except OSError as e:
        raise OSError(f"cannot write the shard times to {a.out}: {e}") from e


def cmd_merge_mutations(a):
    shards = load(Path(a.shards))
    try:
        r = merge_mutations(shards, [Path(p) if p and p != "-" else None for p in a.reports], Path(a.out))
    except (ValueError, ET.ParseError, OSError) as e:
        print(f"merge-mutations: {e}", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(r))


def parse_checkstyle(xml: Path) -> dict:
    issues = []
    for f in parse_report_xml(xml).iter("file"):
        for e in f.iter("error"):
            issues.append(f"{f.get('name')}:{e.get('line')} {e.get('source', '').rsplit('.', 1)[-1]} "
                          f"{e.get('message', '')}"[:240])
    return {"issues": len(issues), "first": issues[:20]}


# ---------------------------------------------------------------- bytecode

def parse_class(data: bytes) -> dict:
    """Minimal JVM class-file reader: class name, referenced classes and members, declared methods."""
    if data[:4] != b"\xca\xfe\xba\xbe":
        raise ValueError("not a class file")
    (n,) = struct.unpack(">H", data[8:10])
    i, k = 10, 1
    cp: list[Any] = [None] * n  # constant pool: None or a (kind, ...) tuple per slot
    while k < n:
        tag = data[i]
        if tag == 1:
            (ln,) = struct.unpack(">H", data[i + 1:i + 3])
            cp[k] = ("utf8", data[i + 3:i + 3 + ln].decode("utf-8", "replace"))
            i += 3 + ln
        elif tag in (3, 4):
            i += 5
        elif tag in (5, 6):
            i += 9
            k += 1
        elif tag in (7, 8, 16, 19, 20):
            cp[k] = ({7: "class", 8: "string", 16: "mtype"}.get(tag, "mod"), struct.unpack(">H", data[i + 1:i + 3])[0])
            i += 3
        elif tag in (9, 10, 11, 12, 17, 18):
            cp[k] = ({12: "nat"}.get(tag, "ref"), *struct.unpack(">HH", data[i + 1:i + 5]))
            i += 5
        elif tag == 15:
            i += 4
        else:
            raise ValueError(f"unknown constant-pool tag {tag}")
        k += 1

    def utf(j):
        return cp[j][1]

    _acc, this_c, _sup = struct.unpack(">HHH", data[i:i + 6])
    i += 6
    (ic,) = struct.unpack(">H", data[i:i + 2])
    i += 2 + 2 * ic

    def skip_attrs(i):
        (ac,) = struct.unpack(">H", data[i:i + 2])
        i += 2
        for _ in range(ac):
            (ln,) = struct.unpack(">I", data[i + 2:i + 6])
            i += 6 + ln
        return i

    (fc,) = struct.unpack(">H", data[i:i + 2])
    i += 2
    for _ in range(fc):
        i = skip_attrs(i + 6)
    (mc,) = struct.unpack(">H", data[i:i + 2])
    i += 2
    methods = []
    for _ in range(mc):
        _a, ni, _d = struct.unpack(">HHH", data[i:i + 6])
        methods.append(utf(ni))
        i = skip_attrs(i + 6)
    classes = {utf(e[1]) for e in cp if e and e[0] == "class"}
    refs = set()
    for e in cp:
        if e and e[0] == "ref" and cp[e[1]] and cp[e[1]][0] == "class" and cp[e[2]] and cp[e[2]][0] == "nat":
            refs.add(f"{utf(cp[e[1]][1])}.{utf(cp[e[2]][1])}")
    return {"name": utf(cp[this_c][1]), "classes": classes, "refs": refs, "methods": methods}


_COMPONENT1 = re.compile(r"^component1(-.+)?$")
_COPY = re.compile(r"^copy(-.+)?$")
_DATA_OBJECT = re.compile(r"\bdata\s+object\s+`?(\w+)`?")


def compiler_generated_members(c: dict, data_objects: set[str]) -> bool:
    """True if the class's toString/hashCode/equals/componentN/copy come from the Kotlin compiler.

    Data classes (componentN/copy, mangled to `component1-<hash>`/`copy-<hash>` when a parameter is a value class),
    value classes (`box-impl`/`unbox-impl`) and data objects (declared `data object X` in main sources). A hand-written
    override of an excluded method is still caught by the source scan in bytecode_checks.
    """
    names = c["methods"]
    if any(_COMPONENT1.match(m) for m in names) and any(_COPY.match(m) for m in names):
        return True
    if "box-impl" in names and "unbox-impl" in names:
        return True
    return re.split(r"[/$]", c["name"])[-1] in data_objects


def kotlin_data_objects(root: Path) -> set[str]:
    """Simple names of `data object` declarations in the main Kotlin sources."""
    out: set[str] = set()
    for _rel, p in iter_files(root, include=env_list("GATE_MAIN_PATHS", "src/main")):
        if p.is_file() and p.suffix == ".kt":
            out |= set(_DATA_OBJECT.findall(strip_code(p.read_text(errors="replace"))))
    return out


def main_classes(root: Path) -> list[tuple[str, dict]]:
    out = []
    for cd in env_list("GATE_CLASS_DIRS", "build/classes/kotlin/main build/classes/java/main"):
        for f in sorted((root / cd).rglob("*.class")) if (root / cd).is_dir() else []:
            out.append((str(f.relative_to(root)), parse_class(f.read_bytes())))
    return out


def read_props(p: Path) -> dict:
    props = {}
    if p.exists():
        for ln in p.read_text().splitlines():
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.split("=", 1)
                props[k.strip()] = v.strip()
    return props


def glob_rx(names: list[str]) -> str:
    return "|".join(re.escape(n).replace(r"\*", r"[A-Za-z0-9_$]*") for n in names)


def strip_code(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"//[^\n]*", " ", text)
    text = re.sub(r'"""(?:.|\n)*?"""', '""', text)
    return re.sub(r'"(?:\\.|[^"\\])*"', '""', text)


def bytecode_checks(root: Path, check) -> dict:
    """Package ownership, forbidden APIs and mutation-hidden methods, from compiled classes."""
    metrics: dict = {}
    classes = main_classes(root)
    check(bool(classes), f"{len(classes)} compiled main classes found in {env('GATE_CLASS_DIRS', 'build/classes/*/main')}")
    pkgs = [p.replace(".", "/") for p in env_list("GATE_MAIN_PACKAGES")]
    foreign = [c["name"] for _, c in classes if not any(c["name"].startswith(p + "/") for p in pkgs)]
    metrics["foreign_classes"] = foreign[:20]
    check(bool(pkgs) and not foreign, f"every compiled main class is inside {env_list('GATE_MAIN_PACKAGES')} "
                                      f"({len(foreign)} outside)")
    fc, fm = env_list("GATE_FORBID_CLASSES"), env_list("GATE_FORBID_METHODS")
    bad = sorted({f"{c['name']} uses {x}" for _, c in classes
                  for x in c["classes"] | c["refs"] if any(x.startswith(f) for f in fc)}
                 | {f"{c['name']} calls {r}" for _, c in classes for r in c["refs"] if any(r.startswith(m) for m in fm)})
    metrics["forbidden_api"] = bad[:20]
    check(not bad, f"no forbidden API in compiled main code (file, process, reflection, network, exit) ({len(bad)} uses)")
    props = read_props(root / env("GATE_PITEST_PROPERTIES", "gradle.properties"))
    targets = sorted(props.get("sdlc.pitestTargets", "").split())
    want = sorted(f"{p}.*" for p in env_list("GATE_MAIN_PACKAGES"))
    check(targets == want, f"mutation targets {targets} equal the owned packages {want}")
    excluded = props.get("sdlc.pitestExcludedMethods", "").split()
    if excluded:
        rx = re.compile(rf"^({glob_rx(excluded)})$")
        hidden = []
        data_objects = kotlin_data_objects(root)
        for _, c in classes:
            data_like = compiler_generated_members(c, data_objects)
            hidden += [f"{c['name']}.{m}" for m in c["methods"] if rx.match(m) and not data_like]
        decl = re.compile(rf"\bfun\s+(?:<[^>]*>\s*)?(?:[\w.?<>, ]+\.)?`?({glob_rx(excluded)})`?\s*[(<]"
                          rf"|@JvmName\s*\(\s*\"({glob_rx(excluded)})\"")
        jdecl = re.compile(rf"(?<![.\w])({glob_rx(excluded)})\s*\([^)]*\)\s*(?:throws[^{{]*)?\{{")
        for rel, p in iter_files(root, include=env_list("GATE_MAIN_PATHS", "src/main")):
            if p.is_file() and p.suffix in (".kt", ".java"):
                t = re.sub(r"\s+", " ", strip_code(p.read_text(errors="replace")))
                if decl.search(t) or (p.suffix == ".java" and jdecl.search(t)):
                    hidden.append(f"{rel}: declares a method excluded from mutation")
        metrics["mutation_hidden"] = hidden[:20]
        check(not hidden, f"no hand-written method is excluded from mutation ({len(hidden)} found)")
    return metrics


# ---------------------------------------------------------------- inventory

def load_inventory(path: Path) -> tuple[list[dict], list[str]]:
    errors = []
    try:
        inv = load(path)
    except (OSError, ValueError) as e:
        return [], [f"cannot read inventory {path}: {e}"]
    cases = inv.get("cases") if isinstance(inv, dict) else None
    if not isinstance(cases, list) or not cases:
        return [], ["inventory must be {\"cases\": [ ... ]} with at least one case"]
    for i, c in enumerate(cases):
        if not isinstance(c, dict):
            errors.append(f"case {i}: not an object")
            continue
        for k in ("class", "name", "kind"):
            if not isinstance(c.get(k), str) or not c.get(k):
                errors.append(f"case {i}: '{k}' must be a non-empty string")
        if c.get("kind") not in (*KINDS, "repro"):
            errors.append(f"case {i}: kind must be one of {KINDS}")
        if not isinstance(c.get("spec"), list) or not all(isinstance(s, str) for s in c.get("spec", [])):
            errors.append(f"case {i}: 'spec' must be a list of spec IDs")
    return cases, errors


def coverage(spec_text: str, cases: list[dict]) -> dict:
    ids = list(dict.fromkeys(m.group(0) for m in SPEC_ID.finditer(spec_text)))
    need_property = [i for i in ids if i.startswith("INV-")]
    need_any = [i for i in ids if i.startswith(("FM-", "AC-"))]
    by_prop = {s for c in cases if c.get("kind") == "property" for s in c.get("spec", [])}
    by_any = {s for c in cases for s in c.get("spec", [])}
    kinds = {c.get("kind") for c in cases}
    return {"ids": ids,
            "uncovered": [i for i in need_property if i not in by_prop] + [i for i in need_any if i not in by_any],
            "missing_kinds": [k for k in KINDS if k not in kinds],
            "unknown_refs": sorted(by_any - set(ids))}


def cmd_inventory_check(a):
    """Validate the tests role's inventory against the spec and a JUnit run against stubs."""
    cases, errors = load_inventory(Path(a.inventory))
    cov = coverage(Path(a.spec).read_text(), cases) if cases else {"uncovered": [], "missing_kinds": list(KINDS)}
    if cov.get("missing_kinds"):
        errors.append(f"no test of kind(s) {cov['missing_kinds']}: the contract requires all of {KINDS}")
    if cov.get("unknown_refs"):
        errors.append(f"inventory cites IDs not in the spec: {cov['unknown_refs']}")
    ran, vacuous = [], []
    if a.junit_dir and cases:
        j = parse_junit(Path(a.junit_dir), a.since)
        for c in cases:
            hits = [x for x in j["cases"] if case_matches(x, c["class"], c["name"])]
            if not hits:
                errors.append(f"inventory case not found in the test run: {c['class']} › {c['name']}")
            elif any(h["status"] == "skipped" for h in hits):
                errors.append(f"inventory case skipped against the stubs (a skip verifies nothing): {c['class']} › {c['name']}")
            elif not any(h["status"] == "failed" for h in hits):
                vacuous.append(f"{c['class']} › {c['name']}")
            ran.extend(hits)
    out = {"ok": not errors, "errors": errors[:40], "cases": len(cases), "coverage": cov,
           "passes_against_stubs": vacuous}
    dump(Path(a.out), out)
    print(json.dumps({"ok": out["ok"], "errors": len(errors), "uncovered": cov.get("uncovered", []),
                      "passes_against_stubs": len(vacuous)}))
    sys.exit(0 if out["ok"] else 1)


def cmd_spec_gaps(a):
    """Collect spec gaps: the tests role's 'Spec gaps' section plus spec IDs no test covers."""
    gaps = []
    tests_md = Path(a.tests_md)
    if tests_md.exists():
        gaps += [f"tests role: {g}" for g in md_section(tests_md.read_text(), "spec gaps")]
    check = load(Path(a.inventory_check)) if Path(a.inventory_check).exists() else {}
    for i in check.get("coverage", {}).get("uncovered", []):
        gaps.append(f"uncovered: {i} has no {'property ' if i.startswith('INV-') else ''}test in the inventory")
    for v in check.get("passes_against_stubs", []):
        gaps.append(f"vacuous: `{v}` passes against TODO() stubs, so it cannot fail")
    Path(a.out).write_text("".join(f"- {g}\n" for g in gaps))
    print(json.dumps({"gaps": len(gaps)}))


def cmd_stub_check(a):
    """O1 guard: files the tests role left under main paths must be interface stubs only."""
    bad = []
    body_ok = re.compile(r"^\s*(TODO\(.*\)|throw\s+NotImplementedError\(.*\)|)\s*$", re.DOTALL)
    for rel in a.files:
        p = Path(a.ws) / rel
        text = re.sub(r"/\*.*?\*/", "", p.read_text(errors="replace"), flags=re.DOTALL)
        text = re.sub(r"//[^\n]*", "", text)
        text = re.sub(r'"(?:\\.|[^"\\])*"', '""', text)
        if re.search(r"\b(if|when|for|while|return|try|catch|do)\b", text):
            bad.append(f"{rel}: control flow in a stub")
        for m in re.finditer(r"^\s*(?:(?:private|internal|public|protected|override|const|lateinit)\s+)*(?:val|var)\s+"
                             r"[^\n=]*=\s*([^\n]+)$", text, re.MULTILINE):
            init = m.group(1).strip()
            if not init.endswith(",") and not body_ok.match(init):
                bad.append(f"{rel}: property initializer `{init[:60]}` (stubs may only declare)")
        for m in re.finditer(r"\b(fun(?!\s+interface\b)|init|get\(\))\b[^{=\n]*(=\s*([^\n]+)|\{)", text):
            if m.group(3) is not None:
                if not body_ok.match(m.group(3).strip()):
                    bad.append(f"{rel}: expression body `{m.group(3).strip()[:60]}`")
                continue
            depth, i = 1, m.end()
            while i < len(text) and depth:
                depth += {"{": 1, "}": -1}.get(text[i], 0)
                i += 1
            if not body_ok.match(text[m.end():i - 1].strip()):
                bad.append(f"{rel}: function body is not TODO(): `{text[m.end():i - 1].strip()[:60]}`")
    print(json.dumps({"ok": not bad, "violations": bad[:20]}))
    sys.exit(0 if not bad else 1)


def cmd_add_test_deps(a):
    """Insert validated test dependencies between the build file's sdlc markers."""
    deps = [ln.strip() for ln in Path(a.deps).read_text().splitlines() if ln.strip() and not ln.startswith("#")]
    rx = re.compile(r"^[A-Za-z0-9_.\-]+:[A-Za-z0-9_.\-]+:[A-Za-z0-9_.+\-]+$")
    bad = [d for d in deps if not rx.match(d)]
    if bad:
        raise SystemExit(f"invalid dependency coordinates: {bad}")
    build = Path(a.build)
    text = build.read_text()
    begin, end = "// sdlc:test-dependencies:begin", "// sdlc:test-dependencies:end"
    if begin not in text or end not in text:
        if not deps:
            return
        raise SystemExit(f"{build} has no '{begin}' / '{end}' markers; cannot add {deps}")
    lines = "\n".join("    " + a.template.replace("%s", d) for d in deps)
    pre, rest = text.split(begin, 1)
    _, post = rest.split(end, 1)
    build.write_text(f"{pre}{begin}\n{lines}\n    {end}{post}")
    print(json.dumps({"added": deps}))


# ---------------------------------------------------------------- gate

def _step_checks(a, r: dict, root: Path, check) -> None:
    """Checks of a gate step that ran to completion (cmd_gate_step handles off, not run and timed out)."""
    if a.name != "integrity":
        check(a.exit == 0, f"command exited {a.exit}")
    if a.name == "integrity":
        lock = Path(a.lock)
        if lock.exists():
            v = verify_lock(root, lock)
            r["metrics"]["lock"] = v
            check(v["ok"], f"locked files unchanged ({v['locked_files']} files; changed={len(v['changed'])}, "
                           f"missing={len(v['missing'])}, added={len(v['added'])})")
        else:
            check(False, "no tests.lock: tests were never locked")
        forbid = env("GATE_FORBID_REGEX")
        if forbid:
            rx, hits = re.compile(forbid), []
            for rel, p in iter_files(root, include=env_list("GATE_MAIN_PATHS", "src/main")):
                if p.is_symlink():
                    hits.append(f"{rel}: symlink in main sources")
                    continue
                for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        hits.append(f"{rel}:{i}: {line.strip()[:120]}")
            r["metrics"]["forbidden"] = hits[:20]
            check(not hits, f"no forbidden patterns in main sources ({len(hits)} hits)")
    elif a.name == "typecheck":
        r["metrics"] = bytecode_checks(root, check)
    elif a.name in ("test", "fuzz"):
        j = parse_junit(root / env("GATE_JUNIT_DIR", "build/test-results/test"), a.started)
        r["metrics"] = {"tests": j["tests"], "failed": len(j["failed"]), "skipped": len(j["skipped"]),
                        "stale_files": j["stale_files"], "failures": j["failed"][:20]}
        min_tests = max(1, int(env("GATE_MIN_TESTS", "1")))
        check(j["tests"] >= min_tests, f"{j['tests']} test cases ran (min {min_tests})"
              + ("; no fresh JUnit XML: was the task UP-TO-DATE? (use --rerun)" if not j["tests"] else ""))
        check(not j["failed"], f"{len(j['failed'])} failed")
        allowed = int(env("GATE_ALLOW_SKIPPED", "0"))
        check(len(j["skipped"]) <= allowed, f"{len(j['skipped'])} skipped (allowed {allowed})")
        inv = env("GATE_TEST_INVENTORY")
        if inv:
            cases, errors = load_inventory(root / inv)
            missing = [f"{c['class']} › {c['name']}" for c in cases
                       if not any(case_matches(x, c["class"], c["name"]) and x["status"] == "passed"
                                  for x in j["cases"])]
            r["metrics"]["inventory_missing"] = missing[:30]
            check(not errors, f"test inventory valid ({len(cases)} cases)" + (f": {errors[:3]}" if errors else ""))
            check(not missing, f"every inventory case ran and passed ({len(cases) - len(missing)}/{len(cases)})")
        else:
            check(False, "GATE_TEST_INVENTORY not set: cannot show that every declared test ran")
    elif a.name == "mutation":
        px = root / env("GATE_MUTATION_REPORT", "build/reports/pitest/mutations.xml")
        min_score = float(env("GATE_MUTATION_MIN", "80"))
        if px.exists() and px.stat().st_mtime + 1 >= a.started:
            p = parse_pitest(px)
            r["metrics"] = {k: p[k] for k in ("total", "killed", "counts", "errors", "score", "score_exact",
                                               "survived")}
            r["metrics"]["min_score"] = min_score
            r["metrics"]["incremental"] = bool(a.incremental)
            if a.incremental:
                check(True, "incremental: Pitest reused the history of an earlier gate; a pass is confirmed by a "
                            "fresh run before it counts")
            check(p["total"] > 0, f"{p['total']} mutants generated")
            check(p["killed"] * 100 >= min_score * p["total"],
                  f"mutation score {p['score_exact']} killed = {p['score']:.2f}% (min {min_score}%, unrounded)")
            max_err = int(env("GATE_MUTATION_MAX_ERRORS", "0"))
            check(p["errors"] <= max_err, f"{p['errors']} run-error/memory/non-viable mutants (max {max_err})")
        else:
            check(False, f"no fresh mutation report at {px.relative_to(root)}")
    elif a.name == "static":
        langs = env_list("GATE_STATIC_LANGS", "kt")
        src_ext = {".kt", ".kts", ".java", ".scala", ".groovy", ".py", ".ts", ".js", ".go", ".rs", ".cs"}
        uncovered = [rel for rel, p in iter_files(root, include=env_list("GATE_MAIN_PATHS", "src/main"))
                     if p.suffix in src_ext and p.suffix.lstrip(".") not in langs]
        check(not uncovered, f"static analysis covers every main source language {langs} "
                             f"({len(uncovered)} files in other languages)" + (f": {uncovered[:3]}" if uncovered else ""))
    if a.name == "static" and env("GATE_RULES"):
        gate_rules(root, check, r["metrics"])
    if a.name == "static" and env("GATE_STATIC_REPORT"):
        sx = root / env("GATE_STATIC_REPORT")
        if sx.exists() and sx.stat().st_mtime + 1 >= a.started:
            r["metrics"] = parse_checkstyle(sx)
            max_issues = int(env("GATE_STATIC_MAX_ISSUES", "0"))
            check(r["metrics"]["issues"] <= max_issues, f"{r['metrics']['issues']} issues (max {max_issues})")
        else:
            check(False, f"no fresh static-analysis report at {env('GATE_STATIC_REPORT')}")


def gate_rules(root: Path, check, metrics: dict) -> None:
    """Amendment B: project rules (ast-grep YAML), adopted by a human via relock, scan the main sources; a match fails
    the step. The directory is a project path (so it is locked with the build); under .sdlc it would not be."""
    rel = env("GATE_RULES")
    rdir = root / rel
    top = Path(rel).parts[0] if Path(rel).parts else ""
    check(not Path(rel).is_absolute() and ".." not in Path(rel).parts and not top.startswith(".sdlc"),
          f"GATE_RULES={rel} is a project path outside .sdlc (so the lock covers it)")
    rules = sorted(rdir.glob("*.yml")) if rdir.is_dir() else []
    check(bool(rules), f"{len(rules)} rule file(s) in {rel}")
    sg = shutil.which(env("GATE_AST_GREP")) if env("GATE_AST_GREP") else (shutil.which("ast-grep") or shutil.which("sg"))
    check(bool(sg), "ast-grep found (a rule check that does not run must not read as a pass)")
    if not (rules and sg):
        return
    hits: list[str] = []
    for rule in rules:
        for src in env_list("GATE_MAIN_PATHS", "src/main"):
            if not (root / src).exists():
                continue
            p = subprocess.run([sg, "scan", "-r", str(rule), "--json=compact", str(root / src)],
                               capture_output=True, text=True)
            if p.returncode not in (0, 1):
                hits.append(f"{rule.name}: ast-grep failed ({p.returncode}): {p.stderr.strip()[:120]}")
                continue
            try:
                found = json.loads(p.stdout or "[]")
            except ValueError:
                hits.append(f"{rule.name}: ast-grep printed invalid JSON")
                continue
            hits += [f"{m.get('ruleId') or rule.stem}: {os.path.relpath(os.path.realpath(m['file']), os.path.realpath(root))}:"
                     f"{m['range']['start']['line'] + 1}" for m in found]
    metrics["rule_matches"] = hits[:20]
    check(not hits, f"{len(rules)} project rule(s) match nothing in main sources ({len(hits)} matches)")

def cmd_gate_step(a):
    d = Path(a.dir)
    (d / "steps").mkdir(parents=True, exist_ok=True)
    root = Path(a.root)
    r = {"name": a.name, "mode": a.mode, "cmd": a.cmd, "exit": a.exit, "log": a.log, "seconds": a.seconds,
         "checks": [], "metrics": {}}

    def check(ok, what: str):
        r["checks"].append({"ok": bool(ok), "what": what})

    if a.mode not in MODES:
        check(False, f"invalid mode {a.mode!r} (must be one of {MODES})")
    elif a.mode == "off":
        pass
    elif a.not_run:
        check(False, f"not run: {a.not_run}")
    elif a.timed_out:
        # The step was killed: its reports are partial (e.g. a half-written XML), so they are not read.
        r["timed_out"] = True
        check(False, f"timed out after {a.seconds}s (limit {a.timeout}s: GATE_{a.name.upper()}_TIMEOUT or GATE_STEP_TIMEOUT): the step was killed, so its "
                     "reports are incomplete and were not read. This is a time limit, not a test result")
    else:
        try:
            _step_checks(a, r, root, check)
        except (ET.ParseError, OSError, ValueError, KeyError, IndexError) as e:
            # A malformed or truncated report is a failed step with a reason, not an evaluator crash.
            check(False, f"{a.name} report unreadable ({e.__class__.__name__}: {str(e)[:200]})")
    r["status"] = "off" if a.mode == "off" and not r["checks"] else (
        "pass" if r["checks"] and all(c["ok"] for c in r["checks"]) else "fail")
    dump(d / "steps" / f"{a.order:02d}-{a.name}.json", r)
    print(f"gate step {a.name}: {r['status']}")


def cmd_gate_report(a):
    d = Path(a.dir)
    expected = a.steps.split()
    steps, problems = [], []
    for i, name in enumerate(expected, 1):
        f = d / "steps" / f"{i:02d}-{name}.json"
        try:
            steps.append(load(f))
        except (OSError, ValueError) as e:
            problems.append(f"step {name}: no readable result ({e.__class__.__name__}); counted as FAIL")
            steps.append({"name": name, "mode": "required", "status": "fail", "seconds": 0, "metrics": {},
                          "checks": [{"ok": False, "what": "evaluator produced no result"}]})
    modes = {s["name"]: s["mode"] for s in steps}
    problems += [f"step '{n}' has invalid mode '{m}' (must be one of {MODES})" for n, m in modes.items() if m not in MODES]
    for m in MANDATORY_STEPS:
        if m not in modes:
            problems.append(f"mandatory step '{m}' is not in GATE_STEPS")
        elif modes[m] != "required":
            problems.append(f"mandatory step '{m}' has mode '{modes[m]}', must be 'required'")
    if len(set(expected)) != len(expected):
        problems.append("duplicate step names in GATE_STEPS")
    if a.fp_before != a.fp_after:
        problems.append("the inputs changed while the gate ran (fingerprint before != after)")
    if a.project_fp and a.project_fp != a.fp_before:
        problems.append("the project changed during the gate run, or the snapshot differs from it")
    if a.config_error:
        problems.append(f"gate.config: {a.config_error}")
    required_failed = [s["name"] for s in steps if s["mode"] == "required" and s["status"] != "pass"]
    ok = not required_failed and not problems
    report = {"pass": ok, "required_failed": required_failed, "problems": problems,
              "optional_failed": [s["name"] for s in steps if s["mode"] == "optional" and s["status"] == "fail"],
              "fingerprint": a.fp_before, "spec_sha": a.spec_sha, "lock_sha": a.lock_sha,
              "config_sha": a.config_sha, "label": a.label, "scratch": a.scratch, "finished": now(), "steps": steps,
              "harness_sha": harness_sha(), "sandbox": sandbox_info(),
              "timed_out": [s["name"] for s in steps if s.get("timed_out")],
              "mutation_incremental": any(s["name"] == "mutation" and bool((s.get("metrics") or {}).get("incremental"))
                                          for s in steps)}
    L = [f"# Gate report: {a.label}", "", f"**Result: {'PASS' if ok else 'FAIL'}**", ""]
    L += [f"- {p}" for p in problems] + ([""] if problems else [])
    if report["timed_out"]:
        L += [f"- Timed out: {', '.join(report['timed_out'])}. A time limit, not a test result. When the mutation step "
              "times out, the orchestrator stops for a human instead of calling the builder again.", ""]
    if report["mutation_incremental"]:
        L += ["- Mutation analysis was incremental (Pitest history from an earlier gate). Not final evidence: a pass "
              "is confirmed by a fresh run.", ""]
    L += [(f"- Fingerprint: `{a.fp_before[:16]}` · spec `{a.spec_sha[:12] or 'none'}` · lock `{a.lock_sha[:12] or 'none'}`"
           f" · config `{a.config_sha[:12]}`"), f"- Finished: {report['finished']}" + (" (scratch)" if a.scratch else ""), "",
          "| step | mode | status | seconds | evidence |", "|---|---|---|---|---|"]
    for s in steps:
        ev = "; ".join(("" if c["ok"] else "FAIL: ") + c["what"] for c in s["checks"]) or "-"
        L.append(f"| {s['name']} | {s['mode']} | {s['status']} | {s.get('seconds', 0)} | {ev} |")
    for s in steps:
        m = s.get("metrics") or {}
        if m.get("failures"):
            L += ["", "## Failing tests", ""] + [f"- `{f['class']}` › {f['name']}: {f['message']}" for f in m["failures"]]
        if m.get("inventory_missing"):
            L += ["", "## Inventory cases not run or not passing", ""] + [f"- {x}" for x in m["inventory_missing"]]
        if s["name"] == "mutation" and m.get("survived"):
            L += ["", f"## Surviving mutants ({len(m['survived'])})", "", "| class | method | line | mutator | status |",
                  "|---|---|---|---|---|"]
            L += [f"| {x['class']} | {x['method']} | {x['line']} | {x['mutator']} | {x['status']} |" for x in m["survived"][:40]]
        if m.get("first"):
            L += ["", f"## Static analysis issues ({m['issues']})", ""] + [f"- {i}" for i in m["first"]]
        for k in ("changed", "missing", "added"):
            if (m.get("lock") or {}).get(k):
                L += ["", f"## Locked files {k}", ""] + [f"- `{x}`" for x in m["lock"][k]]
        if m.get("forbidden"):
            L += ["", "## Forbidden patterns", ""] + [f"- `{x}`" for x in m["forbidden"]]
    L += ["", "Logs: `logs/` next to this file. This report is the only accepted evidence of a pass."]
    (d / "gate-report.md").write_text("\n".join(L) + "\n")
    report["md_sha"] = sha_file(d / "gate-report.md")
    dump(d / "gate-report.json", report)
    print(json.dumps({"pass": ok, "report": str(d / "gate-report.md")}))
    sys.exit(0 if ok else 1)


def latest_gate(project: Path, run: Path):
    """The newest authoritative gate report, checked for integrity and freshness."""
    attempts = sorted((d for d in run.glob("gate/*") if (d / "started.json").exists()),
                      key=lambda d: load(d / "started.json").get("t", 0))
    if not attempts:
        return None, None, ["no gate report on disk"]
    path = attempts[-1] / "gate-report.json"
    problems = []
    if not path.exists():
        return path, None, [f"the newest gate attempt ({attempts[-1].name}) did not finish: it counts as a failure"]
    try:
        g = load(path)
    except (OSError, ValueError) as e:
        return path, None, [f"unreadable gate report {path}: {e}"]
    if g.get("harness_sha") != harness_sha():
        problems.append("the last gate ran with a different version of this skill's scripts")
    md = path.parent / "gate-report.md"
    if not md.exists() or sha_file(md) != g.get("md_sha"):
        problems.append("gate-report.md missing or altered after the gate wrote it")
    if not g.get("pass"):
        problems.append(f"last gate failed: {', '.join(g.get('required_failed', []) + g.get('problems', []))}")
    if g.get("scratch"):
        problems.append("last gate was a scratch run")
    if g.get("mutation_incremental"):
        problems.append("the last gate reused Pitest history (incremental); only a fresh run counts")
    if g.get("fingerprint") != fingerprint(project):
        problems.append("code changed after the last gate run (fingerprint mismatch)")
    approved = run / "spec.approved"
    if not approved.exists() or g.get("spec_sha") != load(approved).get("sha256"):
        problems.append("last gate ran against a different (or unapproved) spec")
    lock = run / "tests.lock"
    if not lock.exists() or g.get("lock_sha") != sha_file(lock):
        problems.append("tests.lock changed after the last gate run")
    return path, g, problems


def cmd_gate_fresh(a):
    _, _, problems = latest_gate(Path(a.project), Path(a.run))
    print(json.dumps({"fresh_pass": not problems, "problems": problems}))
    sys.exit(0 if not problems else 1)


# ---------------------------------------------------------------- findings

FINDING_REQUIRED = ("id", "title", "spec_refs", "test_file", "expected", "actual")
CATEGORIES = ("invariant", "failure-mode", "acceptance", "boundary", "crash-retry-replay", "concurrency",
              "resource", "spec-gap")
TAGS = ("concurrency", "auth", "data-migration", "exactly-once")


def check_findings(data) -> list[str]:
    errs = []
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return ["top level must be an object with a 'findings' array"]
    ids = set()
    for i, f in enumerate(data["findings"]):
        where = f"findings[{i}]"
        if not isinstance(f, dict):
            errs.append(f"{where}: not an object")
            continue
        for k in ("id", "title", "expected", "actual"):
            if not isinstance(f.get(k), str) or not f[k].strip():
                errs.append(f"{where}: '{k}' must be a non-empty string")
        if isinstance(f.get("id"), str) and not ID_RX.match(f["id"]):
            errs.append(f"{where}: id must match {ID_RX.pattern}")
        if f.get("severity") is not None and f.get("severity") not in ("critical", "high", "medium", "low"):
            errs.append(f"{where}: unknown severity {f.get('severity')!r}")
        if isinstance(f.get("spec_refs"), list) and any(not SPEC_ID.fullmatch(str(x)) for x in f["spec_refs"]):
            errs.append(f"{where}: spec_refs must look like INV-1, FM-2, AC-3, NG-4, ESC-5")
        if "test_file" in f and not isinstance(f["test_file"], str):
            errs.append(f"{where}: 'test_file' must be a string")
        if not isinstance(f.get("spec_refs"), list) or not all(isinstance(s, str) for s in f.get("spec_refs") or []):
            errs.append(f"{where}: 'spec_refs' must be a list of strings")
        if f.get("category") is not None and f.get("category") not in CATEGORIES:
            errs.append(f"{where}: unknown category {f.get('category')!r}")
        tags = f.get("escalation_tags", [])
        if not isinstance(tags, list) or any(t not in TAGS for t in tags):
            errs.append(f"{where}: escalation_tags must be a subset of {TAGS}")
        if f.get("id") in ids:
            errs.append(f"{where}: duplicate id {f.get('id')}")
        ids.add(f.get("id"))
    if "attempted" in data and not (isinstance(data["attempted"], list)
                                    and all(isinstance(x, str) for x in data["attempted"])):
        errs.append("'attempted' must be a list of strings")
    return errs


def cmd_check_findings(a):
    try:
        errs = check_findings(load(Path(a.file)))
    except (OSError, ValueError) as e:
        errs = [f"unreadable: {e}"]
    print(json.dumps({"ok": not errs, "errors": errs[:20]}))
    sys.exit(0 if not errs else 1)


# A sandboxed run that did not complete: the sandbox itself failed (97), a time limit (124) or a kill (137, 143, or a
# signal). Its missing reports say nothing about the code: such a run is "incomplete", never "no-build", so an
# infrastructure failure can never discard a finding or a planted bug.
INCOMPLETE_CODES = (97, 124, 137, 143)


def incomplete(code: int) -> bool:
    return code in INCOMPLETE_CODES or code < 0


def run_repro_in(copy: Path, classes: list[str], log: Path, timeout: int):
    """Run some test classes in a disposable copy, sandboxed. Returns (status, failed cases)."""
    tests = " ".join(f"--tests '{c}'" for c in classes)
    cmd = env("GATE_REPRO_CMD", "./gradlew test {tests}").replace("{tests}", tests)
    jd = copy / env("GATE_JUNIT_DIR", "build/test-results/test")
    if jd.exists():
        try:
            shutil.rmtree(jd)
        except OSError as e:  # stale results left behind could be read as this run's: fail
            raise OSError(f"cannot clear stale test results {jd}: {e}") from e
    start = time.time()
    code = run_cmd(sandboxed(cmd, [copy], [Path(env("SDLC_PROJECT", str(copy)))]), copy, timeout, log)
    if incomplete(code):
        return "incomplete", []
    j = parse_junit(jd, start)
    mine = [c for c in j["cases"] if any(c["class"] == k or c["class"].startswith((k + ".", k + "$"))
                                         for k in classes)]
    if not mine:
        return "no-build", []
    failed = [c for c in mine if c["status"] == "failed"]
    return ("failed" if failed else "passed"), failed


ASSERTION_TYPES = re.compile(r"(AssertionError|AssertionFailedError|opentest4j|ComparisonFailure|MultipleFailures)")


def failure_from_code(case: dict) -> bool:
    """A repro failure counts if it is an assertion, or an exception raised from main code."""
    if ASSERTION_TYPES.search(case.get("type", "")) or ASSERTION_TYPES.search(case.get("trace", "")[:300]):
        return True
    frames = re.findall(r"^\s*at ([\w$.]+)\.", case.get("trace", ""), re.MULTILINE)
    pkgs = env_list("GATE_MAIN_PACKAGES")
    return bool(frames) and any(frames[0] == p or frames[0].startswith(p + ".") for p in pkgs)


def fresh_copy(project: Path, tag: str) -> Path:
    d = Path(tempfile.mkdtemp(prefix=f"sdlc-validate-{tag}-", dir=env("TMPDIR") or None))
    for rel, p in iter_files(project):
        out = d / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        if p.is_symlink():
            continue
        shutil.copy2(p, out)
    return d


def cmd_validate_findings(a):
    if a.runs < 2:
        raise SystemExit("validate-findings: --runs must be >= 2 (a repro must fail more than once)")
    project, ws, out = Path(a.project).resolve(), Path(a.workspace).resolve(), Path(a.out)
    (out / "logs").mkdir(parents=True, exist_ok=True)
    try:
        data = parse_json(safe_read(ws, a.findings).decode("utf-8", errors="replace"), a.findings)
    except ValueError as e:
        raise SystemExit(f"findings.json is malformed: {e}") from e
    errs = check_findings(data)
    if errs:
        raise SystemExit(f"findings.json is malformed: {errs[:5]}")
    spec_ids = {m.group(0) for m in SPEC_ID.finditer(Path(a.spec).read_text())}
    existing = {c for _, p in iter_files(project, include=env_list("GATE_TEST_PATHS", "src/test")) for c in test_classes(p)}
    repro_rel = Path(env("GATE_REPRO_DIR", "src/test/kotlin/attack")) / f"round{a.round}"
    results, staged_names = [], set()
    for f in data["findings"]:
        res = {k: f.get(k) for k in ("id", "title", "severity", "category", "spec_refs", "escalation_tags",
                                     "description", "expected", "actual")}
        res.update({"round": a.round, "status": "discarded", "escalation_tags": f.get("escalation_tags", []),
                    "spec_refs": f.get("spec_refs", [])})
        results.append(res)
        tf = f.get("test_file")
        if not tf:
            res["reason"] = "no repro test"
            continue
        src = (ws / tf).resolve()
        if ws not in src.parents or not src.is_file() or src.is_symlink():
            res["reason"] = f"repro test not found in the attacker workspace: {tf}"
            continue
        if not any(r in spec_ids for r in res["spec_refs"]) and f.get("category") != "spec-gap":
            res["reason"] = f"cites no valid spec ID ({res['spec_refs']})"
            continue
        classes = test_classes(src)
        if not classes:
            res["reason"] = "repro file declares no test class"
            continue
        if any(c in existing for c in classes):
            res["reason"] = f"test class name collides with an existing test: {classes}"
            continue
        if not ID_RX.match(res["id"] or ""):
            res["reason"] = f"invalid finding id {res['id']!r}"
            continue
        dest_name = f"{res['id']}_{src.name}"
        if dest_name in staged_names:
            res["reason"] = f"duplicate repro destination {dest_name}"
            continue
        outcomes, failed_cases = [], []
        for n in range(a.runs):
            copy = fresh_copy(project, f"{res['id']}-{n}")
            try:
                dest = copy / repro_rel / src.name
                safe_write(dest, safe_read(ws, str(src.relative_to(ws))))
                st, failed = run_repro_in(copy, classes, out / "logs" / f"{res['id']}-run{n + 1}.log", a.timeout)
            finally:
                shutil.rmtree(copy, ignore_errors=True)
            mine = [c for c in failed if c["name"].startswith(res["id"])]
            if st == "failed" and not mine:
                st = "wrong-case"   # something failed, but not the test that states this finding
            elif st == "failed" and not any(failure_from_code(c) for c in mine):
                st = "not-the-code"  # e.g. error("always"): the failure neither asserts nor comes from main code
            outcomes.append(st)
            if n == 0:
                failed_cases = mine
            if st == "no-build":
                break
        res["runs"] = outcomes
        res["failures"] = [f"{c['name']}: {c['message']}" for c in failed_cases][:5]
        keep_dir = None
        if any(o == "incomplete" for o in outcomes):
            res["status"] = "incomplete"
            res["reason"] = f"a repro run timed out or was killed {outcomes}: validation incomplete, not a verdict"
        elif outcomes and all(o == "failed" for o in outcomes):
            if f.get("category") == "spec-gap":
                res["status"], keep_dir = "spec-gap", out / "spec-gap"
                res["reason"] = "fails, but the spec does not decide it: a human must rule"
            else:
                res["status"] = "reproduced"
                staged = out / "accepted" / dest_name
                safe_write(staged, safe_read(ws, str(src.relative_to(ws))))
                staged_names.add(dest_name)
                res["repro"] = str(repro_rel / dest_name)      # where it goes if the referee upholds it
                res["staged"] = str(staged)
                res["cases"] = [{"class": c["class"], "name": c["name"]} for c in failed_cases]
                existing.update(classes)
        elif "failed" in outcomes and "passed" in outcomes:
            res["status"], keep_dir = "flaky", out / "flaky"
            res["reason"] = f"failed in some runs only {outcomes}: kept out of the gate, escalated"
        else:
            res["reason"] = {"no-build": "repro did not compile or did not run (no-build is not a repro)",
                             "incomplete": "repro run timed out or was killed: not a completed failure",
                             "wrong-case": "the failing test is not the finding's own (its name must start with the id)",
                             "not-the-code": "the failure is neither an assertion nor raised from main code",
                             }.get(next((o for o in outcomes if o != "passed"), "passed"), "repro test passes on current code")
        if keep_dir:
            keep_dir.mkdir(exist_ok=True)
            shutil.copy2(src, keep_dir / src.name)
            res["repro"] = str(keep_dir / src.name)
    dump(out / "findings.validated.json", {"round": a.round, "findings": results})
    summary = {st: sum(1 for r in results if r["status"] == st)
               for st in ("reproduced", "discarded", "flaky", "spec-gap", "incomplete")}
    print(json.dumps({**summary, "accepted_files": [r["repro"] for r in results if r["status"] == "reproduced"]}))


INVENTORY_BASE = "tests-inventory.base.json"


def cmd_inventory_add_repros(a):
    """Append accepted repro cases to the locked inventory (kind 'repro'). Before the first change, the tests role's
    inventory is kept byte for byte (.sdlc/tests-inventory.base.json): its completion record stays verifiable."""
    inv_path = Path(a.inventory)
    run = Path(a.validated).resolve().parents[2]  # .sdlc/attack/round-N/findings.validated.json
    if not (run / INVENTORY_BASE).exists():
        shutil.copyfile(inv_path, run / INVENTORY_BASE)
    inv = load(inv_path)
    for f in load(Path(a.validated))["findings"]:
        if f["status"] == "reproduced":
            for c in f.get("cases", []):
                inv["cases"].append({"class": c["class"], "name": c["name"], "kind": "repro",
                                     "spec": f.get("spec_refs", []), "finding": f"round {f['round']} {f['id']}"})
    dump(inv_path, inv)


def cmd_write_record(a):
    """Completion record, written last: binds a phase result to the inputs it was made from."""
    base = Path(a.out).resolve().parent
    rec = {"kind": a.kind, "at": now(), "fingerprint": a.fingerprint, "spec_sha": a.spec_sha,
           "lock_sha": a.lock_sha, "attacker_cfg": a.attacker_cfg, "harness_sha": harness_sha()}
    for f in a.files:
        rec.setdefault("files", {})[os.path.relpath(Path(f).resolve(), base)] = sha_file(Path(f))
    if a.summary:
        try:
            rec["summary"] = json.loads(a.summary)
        except ValueError as e:
            raise SystemExit(f"--summary is not valid JSON: {e}") from e
    dump(Path(a.out), rec)


def inventory_with_repros_ok(run: Path, inv_path: Path, recorded_sha: str) -> bool:
    """The tests role's inventory plus only accepted repro cases: the kept base matches the record, the current
    inventory is the base with appended 'repro' cases, and each names a finding reproduced in its round."""
    base_p = run / INVENTORY_BASE
    if not base_p.exists() or sha_file(base_p) != recorded_sha:
        return False
    try:
        base, cur = load(base_p), load(inv_path)
    except (OSError, ValueError):
        return False
    if {k: v for k, v in cur.items() if k != "cases"} != {k: v for k, v in base.items() if k != "cases"}:
        return False
    cases = cur.get("cases", [])
    n = len(base.get("cases", []))
    if cases[:n] != base.get("cases", []) or any(c.get("kind") != "repro" for c in cases[n:]):
        return False
    for c in cases[n:]:
        m = re.fullmatch(r"round (\d+) (\S+)", str(c.get("finding", "")))
        if not m:
            return False
        try:
            found = load(run / "attack" / f"round-{m.group(1)}" / "findings.validated.json")["findings"]
        except (OSError, ValueError, KeyError):
            return False
        if not any(f.get("id") == m.group(2) and f.get("status") == "reproduced"
                   and any(x.get("class") == c.get("class") and x.get("name") == c.get("name") for x in f.get("cases", []))
                   for f in found):
            return False
    return True


def record_ok(rec_path: Path, spec_sha: str = "", attacker_cfg: str = "",
              bind_harness: bool = True) -> tuple[dict | None, str]:
    """A completion record is valid only if its files are unchanged and it matches the current identity."""
    try:
        rec = load(rec_path)
    except (OSError, ValueError):
        return None, f"{rec_path.parent.name}: no completion record"
    base = rec_path.resolve().parent
    for f, s in rec.get("files", {}).items():
        p = (base / f).resolve()
        if base.parent not in p.parents and base not in p.parents:
            return None, f"{rec_path.parent.name}: record points outside the run"
        if not p.exists() or sha_file(p) != s:
            run = next((x for x in p.parents if (x / ".sdlc").is_dir()), None)
            if p.exists() and run and p.name == Path(env("GATE_TEST_INVENTORY", "tests-inventory.json")).name \
                    and inventory_with_repros_ok(run / ".sdlc", p, s):
                continue  # only accepted repros were appended (attack rounds)
            return None, f"{rec_path.parent.name}: {p.name} changed after completion"
    if spec_sha and rec.get("spec_sha") != spec_sha:
        return None, f"{rec_path.parent.name}: made under a different spec"
    run = next((p for p in rec_path.resolve().parents if p.name == ".sdlc"), None)
    if run and rec.get("lock_sha") and rec["lock_sha"] not in {h["after"] for h in lock_history(run)}:
        return None, f"{rec_path.parent.name}: made against a lock that is not in this run's lock history"
    if attacker_cfg and rec.get("kind") == "attack" and rec.get("attacker_cfg") != attacker_cfg:
        return None, f"{rec_path.parent.name}: made with a different attacker configuration"
    if bind_harness and rec.get("harness_sha") != harness_sha():
        return None, f"{rec_path.parent.name}: made by a different version of this skill's scripts"
    return rec, ""


def cmd_spec_edit(a):
    """At approval: if spec.md differs from the spec role's output, record that a human edited it.

    Writes spec.edited (role sha -> approved sha, who, how) and spec.edit.diff when the role's copy
    (spec.role.md) exists. Removes a stale record when the spec is the role's output again."""
    run = Path(a.run)
    spec, edited, diff_path = run / "spec.md", run / "spec.edited", run / "spec.edit.diff"
    try:
        from_sha = load(run / "spec.done").get("spec_sha", "")
    except (OSError, ValueError):
        from_sha = ""
    to_sha = sha_file(spec)
    if not from_sha or from_sha == to_sha:
        for p in (edited, diff_path):
            p.unlink(missing_ok=True)
        return
    diff_sha = None
    role_copy = run / "spec.role.md"
    if role_copy.exists() and sha_file(role_copy) == from_sha:
        import difflib
        diff_path.write_text("".join(difflib.unified_diff(
            role_copy.read_text().splitlines(keepends=True), spec.read_text().splitlines(keepends=True),
            "spec.role.md (spec role output)", "spec.md (approved)")))
        diff_sha = sha_file(diff_path)
    else:
        diff_path.unlink(missing_ok=True)
    dump(edited, {"from_sha": from_sha, "to_sha": to_sha, "diff": diff_path.name if diff_sha else None,
                  "diff_sha": diff_sha, "how": a.how, "note": a.note, "user": os.environ.get("USER", "unknown"),
                  "session_id": os.environ.get("PI_SESSION_ID"), "at": now()})
    print(f"spec.md differs from the spec role's output: human edit recorded in {edited.name}"
          + (f" ({diff_path.name})" if diff_sha else " (no diff: the role's copy is missing)"))


def spec_done_ok(run: Path, spec_sha: str) -> tuple[dict | None, str, str]:
    """spec.done is valid as recorded, or it is the role's output plus a recorded, approved human edit.

    Returns (record, error, note); note describes an accepted human edit."""
    rec, err = record_ok(run / "spec.done", spec_sha, bind_harness=False)
    if rec:
        return rec, "", ""
    try:
        edit, raw = load(run / "spec.edited"), load(run / "spec.done")
    except (OSError, ValueError):
        return None, err, ""
    if edit.get("to_sha") != spec_sha or not edit.get("from_sha") or raw.get("spec_sha") != edit["from_sha"]:
        return None, err, ""
    base = (run / "spec.done").resolve().parent
    for f, s in raw.get("files", {}).items():
        p = (base / f).resolve()
        if p == base / "spec.md":
            if s != edit["from_sha"]:
                return None, err, ""
        elif base not in p.parents or not p.exists() or sha_file(p) != s:
            return None, err, ""
    if edit.get("diff"):
        if edit["diff"] != "spec.edit.diff":  # the only name cmd_spec_edit writes; never follow another path
            return None, "spec.edited names an unexpected diff file", ""
        d = base / "spec.edit.diff"
        if not d.exists() or sha_file(d) != edit.get("diff_sha"):
            return None, "spec.edit.diff changed after approval", ""
    where = f" (diff: .sdlc/{edit['diff']})" if edit.get("diff") else " (no diff recorded)"
    return raw, "", f"spec.md was edited by a human after the spec role and approved with the edit, {edit.get('how')}{where}"


def cmd_record_check(a):
    rec, err = record_ok(Path(a.record), a.spec_sha, a.attacker_cfg)
    print(json.dumps({"ok": rec is not None, "error": err, "record": rec}))
    sys.exit(0 if rec is not None else 1)


# ---------------------------------------------------------------- calibration

def apply_mutant(root: Path, m: dict) -> None:
    f = contained(Path(root), m["file"], env_list("GATE_MAIN_PATHS", "src/main"))
    text = f.read_text()
    n = text.count(m["old"])
    if n != 1:
        raise ValueError(f"mutant {m.get('name')!r}: 'old' occurs {n} times in {m['file']} (must be exactly once)")
    f.write_text(text.replace(m["old"], m["new"]))


def cmd_mutate(a):
    for m in load(Path(a.mutants)):
        apply_mutant(Path(a.root), m)
        print(f"planted: {m['name']}")


def cmd_check_mutants(a):
    """Keep planted bugs that apply exactly once and build; record whether the locked suite catches each."""
    project = Path(a.project)
    raw = load(Path(a.mutants))
    if not isinstance(raw, list):
        raise SystemExit("mutants file must be a JSON list")
    kept, report = [], []
    for i, m in enumerate(raw, 1):
        if not (isinstance(m, dict) and all(isinstance(m.get(k), str) for k in ("name", "file", "old", "new"))):
            report.append({"mutant": i, "status": "malformed"})
            continue
        try:
            contained(project, m["file"], env_list("GATE_MAIN_PATHS", "src/main"))
        except ValueError as e:
            report.append({"mutant": m["name"], "status": f"rejected: {e}"})
            continue
        copy = fresh_copy(project, f"mut{i}")
        try:
            apply_mutant(copy, m)
            st, _ = run_repro_in(copy, [c for _, p in iter_files(copy, include=env_list("GATE_TEST_PATHS", "src/test"))
                                        for c in test_classes(p)] or ["*"], Path(a.logs) / f"mutant{i}.log", a.timeout)
        except ValueError as e:
            st = f"rejected: {e}"
        finally:
            shutil.rmtree(copy, ignore_errors=True)
        status = {"failed": "kept (suite catches it)", "passed": "kept (suite misses it)"}.get(st, st)
        report.append({"mutant": m["name"], "status": status})
        if st in ("failed", "passed"):
            kept.append({**m, "suite": "caught" if st == "failed" else "missed"})
    dump(Path(a.out), kept)
    print(json.dumps({"kept": len(kept), "report": report}))
    if any(r["status"] == "incomplete" for r in report):
        print("check-mutants: a sandboxed run did not complete (sandbox failure or time limit): not a verdict",
              file=sys.stderr)
        sys.exit(5)
    sys.exit(0 if kept else 1)


def cmd_calibrate_score(a):
    """Score calibration: one sandboxed run per code variant (original, each single mutant), all repros at once."""
    cal, orig = Path(a.cal), Path(a.original)
    mutants = load(Path(a.mutants))
    found = load(cal / "attack" / "findings.validated.json")["findings"]
    repros = [f for f in found if f["status"] == "reproduced"]
    classes_of = {f["id"]: test_classes(Path(f["staged"])) for f in repros}

    def statuses(tag: str, subset: list[dict], mutant: dict | None = None) -> dict[str, str]:
        """Run the subset's repros together on a copy of the original (+ mutant). id -> failed|passed|no-build."""
        if not subset:
            return {}
        copy = fresh_copy(orig, tag)
        try:
            if mutant:
                apply_mutant(copy, mutant)
            for f in subset:
                dest = copy / f["repro"]
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(f["staged"], dest)
            tests = " ".join(f"--tests '{c}'" for f in subset for c in classes_of[f["id"]])
            jd = copy / env("GATE_JUNIT_DIR", "build/test-results/test")
            start = time.time()
            cmd = env("GATE_REPRO_CMD", "./gradlew test {tests}").replace("{tests}", tests)
            code = run_cmd(sandboxed(cmd, [copy], [Path(env("SDLC_PROJECT", str(copy)))]), copy, a.timeout,
                           cal / "logs" / f"{tag}.log")
            if incomplete(code):
                print(f"calibrate-score: run {tag} did not complete (exit {code}): calibration incomplete, not a verdict",
                      file=sys.stderr)
                sys.exit(5)
            cases = parse_junit(jd, start)["cases"]
        finally:
            shutil.rmtree(copy, ignore_errors=True)
        out = {}
        for f in subset:
            mine = [c for c in cases if any(c["class"] == k or c["class"].startswith((k + ".", k + "$"))
                                            for k in classes_of[f["id"]])]
            out[f["id"]] = "no-build" if not mine else ("failed" if any(c["status"] == "failed" for c in mine)
                                                         else "passed")
        if len(subset) > 1 and all(v == "no-build" for v in out.values()):
            # One repro that does not compile on this variant breaks the batch: fall back to one at a time.
            for f in subset:
                out.update(statuses(f"{tag}-{f['id']}", [f], mutant))
        return out

    on_original = statuses("original", repros)
    for f in repros:
        f["on_original"] = on_original.get(f["id"], "no-build")
    disc = [f for f in repros if f["on_original"] == "passed"]
    per = []
    for i, m in enumerate(mutants, 1):
        st = statuses(f"mutant{i}", disc, m)
        per.append({"mutant": m["name"], "suite": m.get("suite"),
                    "caught_by": [fid for fid, v in st.items() if v == "failed"]})
    out = {"model": a.model, "attacker_cfg": a.attacker_cfg, "fingerprint": a.fingerprint, "at": now(),
           "planted": len(mutants), "caught": sum(1 for p in per if p["caught_by"]), "per_mutant": per,
           "findings": len(found), "reproduced_on_mutant": len(repros), "discriminating": len(disc),
           "also_fail_on_original": [f["id"] for f in repros if f["on_original"] == "failed"],
           # Findings whose validation did not complete (sandbox failure, timeout, kill): not scored, and named, so a
           # "missed" bug is never silently one an unvalidated repro might have caught.
           "incomplete_findings": [f["id"] for f in found if f.get("status") == "incomplete"]}
    dump(cal / "calibration.json", out)
    print(json.dumps({k: out[k] for k in ("planted", "caught", "discriminating", "also_fail_on_original")}))


# ---------------------------------------------------------------- escalation

ESCALATION = {
    "concurrency": (r"\b(synchronized|@Synchronized|Mutex|Semaphore|ReentrantLock|ReadWriteLock|Atomic(Reference|Long|"
                    r"Integer|Boolean)|ConcurrentHashMap|@Volatile|volatile|Thread\b|ExecutorService|CompletableFuture|"
                    r"launch\s*\{|async\s*\{|runBlocking|coroutineScope|Dispatchers\.|concurren\w*|race\b|"
                    r"thread-safe|thread safe|parallel|spawn|tokio|goroutine|chan\b|Arc<|RwLock)"),
    "auth": (r"\b(auth\w*|jwt|oauth\w*|password\w*|bearer|credential\w*|permission\w*|rbac|acl|secret\w*|"
             r"api[_-]?key\w*|session[_-]?token)\b"),
    "data-migration": (r"\b(migrat\w*|ALTER\s+TABLE|CREATE\s+TABLE|DROP\s+TABLE|schema[_ -]?version|flyway|"
                       r"liquibase|backfill\w*|persist\w*\s+format)\b"),
    "exactly-once": (r"(exactly[- ]once|idempoten\w*|at[- ]least[- ]once|at[- ]most[- ]once|\breplay\w*|"
                     r"offset commit|outbox|\btransactional\b)"),
}


def md_section(text: str, title_rx: str) -> list[str]:
    """Bullet items of the first '## ...<title>...' section, wrapped lines joined."""
    m = re.search(rf"^##\s+.*{title_rx}.*$", text, re.MULTILINE | re.IGNORECASE)
    if not m:
        return []
    rest = text[m.end():]
    nxt = re.search(r"^##\s", rest, re.MULTILINE)
    items: list[str] = []
    for ln in (rest[:nxt.start()] if nxt else rest).splitlines():
        if re.match(r"[-*]\s", ln) or (re.match(r"\s+[-*]\s", ln) and not items):
            items.append(re.sub(r"^\s*[-*]\s+", "", ln).strip())
        elif re.match(r"\s+[-*]\s", ln):  # a sub-bullet: part of the item above
            items[-1] += " " + re.sub(r"^\s*[-*]\s+", "", ln).strip()
        elif ln.strip() and items and not ln.lstrip().startswith(("|", "#", "```")):
            items[-1] += " " + ln.strip()
    return [i for i in items if i]


def is_text(p: Path) -> bool:
    try:
        return b"\0" not in p.read_bytes()[:4096]
    except OSError:
        return False


def escalate(project: Path, run: Path) -> tuple[str, list[str], int]:
    spec_text = (run / "spec.md").read_text() if (run / "spec.md").exists() else ""
    cats: dict[str, list[str]] = {k: [] for k in [*ESCALATION, "unclassified"]}
    esc_items = md_section(spec_text, "escalation")
    disposed: dict[str, str] = {}
    disp = re.compile(r"ESC-\d+\s*:\s*(concurrency|auth|data-migration|exactly-once)\s*:\s*(touches|does-not-touch|unknown)\b",
                      re.IGNORECASE)
    for item in esc_items:
        m = disp.search(item)
        if not m:
            cats["unclassified"].append(f"spec (no structured disposition): {item}")
            continue
        c, d = m.group(1).lower(), m.group(2).lower()
        disposed[c] = d
        if d != "does-not-touch":
            cats[c].append(f"spec ({d}): {item}")
    for c in ESCALATION:   # every mandatory category needs an explicit disposition
        if c not in disposed:
            cats[c].append(f"no disposition: the spec does not say whether this touches '{c}'; a human must decide")
    code: dict[str, dict[str, list[str]]] = {k: {} for k in ESCALATION}
    for rel, p in iter_files(project, include=env_list("GATE_MAIN_PATHS", "src/main")):
        if p.is_symlink() or not is_text(p):
            continue
        for i, line in enumerate(p.read_text(errors="replace").splitlines(), 1):
            if re.match(r"\s*package\b", line):
                continue
            for c, rx in ESCALATION.items():
                if re.search(rx, line, re.IGNORECASE):
                    code[c].setdefault(rel, []).append(f"{i}: {line.strip()[:100]}")
    unresolved = []
    for fp in sorted(run.glob("attack/round-*/findings.validated.json")):
        for f in load(fp)["findings"]:
            if f["status"] == "discarded":   # an unproven allegation changes nothing
                continue
            for t in f.get("escalation_tags", []):
                if t in cats:
                    cats[t].append(f"finding {f['id']} (round {f['round']}, {f['status']}): {f['title']}")
            if f["status"] in ("flaky", "spec-gap", "rejected-by-referee"):
                unresolved.append(f"**{f['status']}** finding {f['id']} (round {f['round']}): {f['title']} — "
                                  f"{f.get('reason', '')}")
    gaps = run / "spec-gaps.md"
    if gaps.exists() and gaps.read_text().strip():
        accepted = (run / "spec-gaps.accepted").exists() and load(run / "spec-gaps.accepted").get("sha256") == sha_file(gaps)
        for g in gaps.read_text().splitlines():
            if g.strip():
                unresolved.append(f"**spec gap{' (accepted by human)' if accepted else ''}**: {g.lstrip('- ')}")
    disputes = run / "disputes.md"
    if disputes.exists():
        unresolved += [f"**open dispute** (builder vs locked test): {ln.strip()}" for ln in disputes.read_text().splitlines()
                       if ln.strip()]
    out = ["# Escalations: human review required", "",
           ("Regardless of gate results. Every spec escalation trigger is kept; triggers that match no category are "
            "'unclassified'. Code hits come from a keyword scan of main sources, which over-flags on purpose."), ""]
    flagged = []
    for c, items in cats.items():
        files = code.get(c, {})
        if not items and not files:
            continue
        flagged.append(c)
        out += [f"## {c} — HUMAN REVIEW REQUIRED", ""] + [f"- {x}" for x in items]
        for rel, hits in files.items():
            out.append(f"- code `{rel}` ({len(hits)} lines): " + "; ".join(f"`{h}`" for h in hits[:3])
                       + (" …" if len(hits) > 3 else ""))
        out.append("")
    if not flagged:
        out.append("No category flagged. Keyword scan and spec triggers only; absence is not proof.")
    if unresolved:
        out += ["## Unresolved by the loop", ""] + [f"- {u}" for u in unresolved] + [""]
    text = "\n".join(out) + "\n"
    (run / "escalations.md").write_text(text)
    return text, flagged, len(unresolved)


def cmd_escalate(a):
    _, flagged, unresolved = escalate(Path(a.project), Path(a.run))
    print(json.dumps({"categories": flagged, "unresolved": unresolved}))


# ---------------------------------------------------------------- final report

def model_family(model: str) -> str:
    name = model.rsplit("/", 1)[-1].lower()
    for fam in ("claude", "gemini", "gpt", "o1", "o3", "o4", "llama", "mistral", "deepseek", "qwen", "nova", "grok"):
        if name.startswith(fam) or f".{fam}" in name or f"{fam}-" in name:
            return fam
    return name


def consolidation_lines(project: Path, run: Path) -> list[str]:
    """B5: the report's Consolidation section (empty when no round was consolidated)."""
    spec = importlib.util.spec_from_file_location("consolidate", Path(__file__).with_name("consolidate.py"))
    if spec is None or spec.loader is None:
        return []
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mains = [p for _, p in iter_files(project, include=env_list("GATE_MAIN_PATHS", "src/main"))]
    return mod.report_section(run, mains)


def cmd_report(a):
    project, run = Path(a.project), Path(a.run)
    spec = run / "spec.md"
    spec_text = spec.read_text() if spec.exists() else ""
    why: list[str] = []
    phase_notes: list[str] = []
    approval = load(run / "spec.approved") if (run / "spec.approved").exists() else None
    spec_sha = sha_file(spec) if spec.exists() else ""
    if not approval or approval.get("sha256") != spec_sha:
        why.append("spec not approved, or changed since approval")
    lock_path = run / "tests.lock"
    if lock_path.exists():
        v = verify_lock(project, lock_path)
        if not v["ok"]:
            why.append("locked files changed since locking")
        elif v["spec_sha"] != spec_sha:
            why.append("tests were written for a different spec")
    else:
        why.append("tests never locked")
    for name, what in (("spec.done", "spec role"), ("tests.done", "tests role")):
        # Not bound to the harness version: re-running these needs a new human approval. The harness
        # that made them is shown under residual risks when it differs.
        if name == "spec.done":
            prec, perr, edit_note = spec_done_ok(run, spec_sha)
            if edit_note:
                phase_notes.append(edit_note)
        else:
            prec, perr = record_ok(run / name, spec_sha, bind_harness=False)
        if not prec:
            why.append(f"no valid {what} completion record ({perr}): the phase must run as a separate role")
        elif prec.get("harness_sha") != harness_sha():
            phase_notes.append(f"the {what} phase ran with an earlier version of this skill's scripts")
    if (run / "repair").exists():
        why.append("a test repair is staged and awaits a human (accept-repair)")
    gate_path, gate, gate_problems = latest_gate(project, run)
    why += gate_problems
    gaps = run / "spec-gaps.md"
    if gaps.exists() and gaps.read_text().strip():
        acc = run / "spec-gaps.accepted"
        if not (acc.exists() and load(acc).get("sha256") == sha_file(gaps)):
            why.append("spec gaps found by the tests role await a human decision")
    disputes = run / "disputes.md"
    if disputes.exists() and disputes.read_text().strip():
        why.append("open builder disputes await a human ruling")
    current_fp = fingerprint(project)
    max_rounds = int(env("SDLC_MAX_ROUNDS", "3"))
    rounds, findings = [], []
    for r in range(1, max_rounds + 1):
        d = run / "attack" / f"round-{r}"
        if not d.exists():
            break
        if (d / "attacker.failed").exists():
            why.append(f"attack round {r} did not complete (attacker error): not a clean round")
            break
        rec, err = record_ok(d / "attacker.done", spec_sha, a.attacker_cfg)
        if not rec:
            why.append(f"attack round {r}: {err}")
            break
        rounds.append({"round": r, **rec})
        findings += load(d / "findings.validated.json")["findings"]
    reproduced = [f for f in findings if f["status"] == "reproduced"]
    last = rounds[-1] if rounds else None
    final_unattacked = False
    if last is None:
        why.append("no attack round completed")
    elif last["summary"]["reproduced"] == 0:
        if last["fingerprint"] != current_fp:
            why.append(f"the dry round {last['round']} attacked older code than the current code")
    elif last["round"] < max_rounds:
        why.append(f"round {last['round']} had findings but round {last['round'] + 1} has not run")
    else:
        final_unattacked = True
    if env("SDLC_DESIGN", "0") == "1":
        drec, derr = record_ok(run / "design" / "design.done", spec_sha)
        if not drec:
            why.append(f"design phase required (SDLC_DESIGN=1) but not valid: {derr}")
    if not env("SDLC_MODEL_attack") and not env("SDLC_RUNNER_attack"):
        why_note = "attacker model not configured: it may be the same as the builder's"
    else:
        why_note = ""
    cals = sorted(run.glob("calibration/*/calibration.json"), key=lambda p: p.stat().st_mtime)
    cal = load(cals[-1]) if cals else None
    cal_bound = bool(cal) and cal.get("fingerprint") == current_fp and cal.get("attacker_cfg") == a.attacker_cfg
    esc_text, esc_cats, unresolved = escalate(project, run)
    done = not why
    status = "NOT DONE"
    if done:
        status = "DONE" if not (esc_cats or unresolved) else "GATE PASSED — HUMAN REVIEW REQUIRED BEFORE MERGE"

    L = [f"# Adversarial SDLC report: {project.name}", "", f"**Status: {status}**", ""]
    if why:
        L += ["Not done because:", ""] + [f"- {w}" for w in why] + [""]
    if gate_path:
        L += [f"Evidence: `{gate_path.parent.relative_to(project)}/gate-report.md`"
              + (f" (fingerprint `{gate['fingerprint'][:16]}`)" if gate else ""), ""]
    L += ["## What was verified", ""]
    if approval:
        rel = (f"relayed by agent; verify in session `{approval.get('session_id', 'unknown')}`"
               if approval.get("how") != "typed at terminal" else "typed at terminal")
        L.append(f"- Spec approved ({rel}; {approval.get('at')}; sha `{approval.get('sha256', '')[:12]}`).")
    if gate and not gate_problems:
        for s in gate["steps"]:
            if s["status"] == "pass":
                L.append(f"- Gate `{s['name']}`: " + "; ".join(c["what"] for c in s["checks"]))
    L.append(f"- Attack rounds completed: {len(rounds)}; reproduced findings: {len(reproduced)}, each now a locked "
             f"regression test in the inventory.")
    if cal and cal_bound:
        L.append(f"- Attacker calibration ({cal['model']}): {cal['caught']}/{cal['planted']} planted bugs caught by repros "
                 f"that fail on the bug and pass on the real code."
                 + (f" Not scored, validation incomplete: {', '.join(cal['incomplete_findings'])}."
                    if cal.get("incomplete_findings") else ""))
    inv_check = run / "inventory-check.json"
    if inv_check.exists():
        ic = load(inv_check)
        L.append(f"- Test inventory: {ic['cases']} declared cases; before the build, all ran against TODO() stubs; "
                 f"every INV has a property case and every FM/AC a case, or is listed as a spec gap.")
    L += ["", "## What was not verified", ""]
    if gate:
        L += [f"- Gate `{s['name']}` {s['status']} ({s['mode']})." for s in gate["steps"] if s["status"] != "pass"]
    if gaps.exists() and gaps.read_text().strip():
        L += [f"- Spec gap: {g.lstrip('- ')}" for g in gaps.read_text().splitlines() if g.strip()]
    if final_unattacked:
        L.append(f"- The fixes after round {max_rounds} were not attacked again (round limit). Only the gate and the "
                 f"reproduced-finding tests checked them.")
    L += [f"- Out of scope: {x}" for x in md_section(spec_text, "non-goal")]
    L += [f"- Decided by default, not by a human: {x}" for x in md_section(spec_text, "(open|unknown)")]
    L.append("- Anything outside the test process: performance, multi-process or multi-node behaviour, real "
             "infrastructure, deployment.")
    L += ["", "## Residual risks", "",
          "The gate shows that these tests and tools found nothing more. It does not show that the code is correct.", ""]
    if gate:
        for s in gate["steps"]:
            if s["name"] == "mutation" and s.get("metrics", {}).get("survived"):
                sv = s["metrics"]["survived"]
                L.append(f"- {len(sv)} surviving or uncovered mutants ({s['metrics'].get('score_exact')} killed): behaviour "
                         f"changes no test detects. None is claimed equivalent.")
                L += [f"  - `{x['class']}.{x['method']}` line {x['line']}: {x['description']} ({x['status']})" for x in sv[:10]]
    disc = [f for f in findings if f["status"] == "discarded"]
    if disc:
        L.append(f"- {len(disc)} attacker findings discarded without a reproducing test: unverified, not disproved.")
        L += [f"  - {f['id']} (round {f['round']}): {f['title']} — {f.get('reason', '')}" for f in disc[:10]]
    roles = read_jsonl(run / "roles.jsonl")
    model_of: dict[str, set] = {}
    for r in roles:
        model_of.setdefault(r["role"], set()).add(r.get("model") or "(pi default)")
    if why_note:
        L.append(f"- {why_note}.")
    am, bm = model_of.get("attack", set()), model_of.get("build", set())
    if am & bm:
        L.append(f"- Attacker and builder shared a model ({', '.join(sorted(am & bm))}).")
    elif {model_family(m) for m in am} & {model_family(m) for m in bm}:
        L.append("- Attacker and builder are the same model family: blind spots may be shared.")
    if rounds and not reproduced:
        if cal and cal_bound:
            L.append(f"- No round produced a reproducible finding. The calibrated attacker caught {cal['caught']}/"
                     f"{cal['planted']} planted bugs, so this is evidence the code resists this attacker. It is not proof.")
        else:
            L.append("- No round produced a reproducible finding, and no calibration exists for this code and attacker "
                     "configuration. The report cannot tell a robust implementation from a weak attacker."
                     + (" (A calibration exists, but for older code or another attacker configuration.)" if cal else ""))
    unsandboxed = sorted({r["role"] for r in roles if r.get("sandbox") not in SANDBOXED_KINDS})
    if unsandboxed:
        L.append(f"- Roles that ran without an OS sandbox ({', '.join(unsandboxed)}): those processes could read or write "
                 f"anything the user can.")
    backends = {r.get("sandbox") for r in roles} | {(gate or {}).get("sandbox", {}).get("backend")}
    if "docker" in backends:
        imgs = sorted({v[7:19] for r in roles for v in (r.get("sandbox_images") or {}).values()})
        L.append("- Docker backend (" + (", ".join(f"`{i}`" for i in imgs) or "images unrecorded") + "): each role "
                 "and each code-under-test run had its own container, with only its own directories mounted, "
                 "no credentials, and CPU, memory and process limits. Code under test had no network. Model "
                 "roles could reach only the model gateway, through the egress proxy, so they can still send "
                 "anything they can read to the model provider. The containers share the Docker VM's kernel "
                 "(runc): kernel or VM escapes are out of scope.")
    k8s = ((gate or {}).get("sandbox") or {}).get("code")
    if k8s:
        L.append(f"- Code under test (gate steps, repros) ran on Kubernetes ({k8s.get('context')}/{k8s.get('namespace')}, "
                 f"image `{str(k8s.get('image'))[-19:]}`): one pod per call with no network, no credentials and Pod "
                 "Security restricted. The cluster, its nodes and its API server are trusted. Inside the pod the "
                 "read-only inputs are mounted read-only (kernel-enforced) and compared file by file with the laptop's copy before "
                 "the code runs. A role's own checks run in one warm pod per role, reused across that role's checks: they are "
                 "advisory, never evidence.")
    if backends & {"sandbox-exec"} or not backends - {None}:
        L.append(f"- sandbox-exec backend: code under test (gate steps, repros, stub runs) could reach only loopback, "
                 f"which includes any local service, such as a model gateway. Model roles ran with network "
                 f"'{env('SDLC_SANDBOX_NET_ROLES', 'all')}'. CPU and memory are not limited. A Gradle or Kotlin "
                 f"daemon started outside the sandbox could be reused by a build that re-enables daemons.")
    if approval and approval.get("how") != "typed at terminal":
        L.append(f"- Spec approval was relayed by an agent (\"{approval.get('note', '')}\"), not typed by the human.")
    L += [f"- {n}." for n in phase_notes]
    L.append("- Forbidden APIs and mutation-hidden methods are checked on compiled classes; the stub check and the "
             "source forbid-regex are heuristics; inventory kinds are the tests role's own labels.")
    L += consolidation_lines(project, run)
    L += ["", "## Escalation list", "", re.sub(r"^## ", "### ", esc_text.split("\n", 2)[-1].strip(), flags=re.MULTILINE), ""]
    L += ["## Roles and models", "", "| role | runs | failed | sandboxed | model(s) |", "|---|---|---|---|---|"]
    for role, models in model_of.items():
        mine = [r for r in roles if r["role"] == role]
        L.append(f"| {role} | {len(mine)} | {sum(1 for r in mine if r['exit'] != 0)} | "
                 f"{sum(1 for r in mine if r.get('sandbox') in SANDBOXED_KINDS)}/{len(mine)} | "
                 f"{', '.join(sorted(models))} |")
    L += ["", "## Findings by round", "", "| round | id | status | spec | title | reason |", "|---|---|---|---|---|---|"]
    L += [f"| {f['round']} | {f['id']} | {f['status']} | {', '.join(f.get('spec_refs') or [])} | {(f.get('title') or '')[:70]} | "
          f"{f.get('reason', '')[:70]} |" for f in findings] or ["| - | - | - | - | none | |"]
    tmp = run / "REPORT.md.tmp"
    tmp.write_text("\n".join(L) + "\n")
    os.replace(tmp, run / "REPORT.md")
    print(json.dumps({"status": status, "why": why, "report": str(run / "REPORT.md")}))
    sys.exit(0 if done else 1)


# ---------------------------------------------------------------- judge


# upgrade (speed spec A5): bring a project created by an older skill to the current templates' harness parts.
# build.gradle.kts: a three-way merge (base = the template the project was created from, ours = the project's file,
# theirs = the current template), so the project's own edits stay. src/sdlcPitest: replaced from the template.
# gate.config: keys of the template that the project lacks are added; existing values are never changed.
# gradle.properties and sdlc.config hold project choices (strength, threads, excluded tests) and are not touched.
UPGRADE_MERGED = ["build.gradle.kts"]
UPGRADE_DIRS = {"src/sdlcPitest": "kotlin/sdlcPitest"}


def render_template(text: str, project: Path) -> str:
    pkg = ""
    gc = project / "gate.config"
    if gc.exists():
        m = re.search(r'^GATE_MAIN_PACKAGES="?([^"\s]+)', gc.read_text(), re.M)
        pkg = m.group(1) if m else ""
    return text.replace("__PACKAGE__", pkg or "CHANGE_ME").replace("__NAME__", project.name)


def template_versions(skill_dir: Path, rel: str) -> list[tuple[str, str]]:
    """(commit, text) of every committed version of a template file, newest first (needs the skill's git repo)."""
    top = subprocess.run(["git", "-C", str(skill_dir), "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    if top.returncode != 0:
        return []
    path = str((skill_dir / "templates" / rel).resolve().relative_to(Path(top.stdout.strip()).resolve()))
    commits = subprocess.run(["git", "-C", top.stdout.strip(), "log", "--format=%H", "--", path],
                             capture_output=True, text=True).stdout.split()
    out = []
    for c in commits:
        r = subprocess.run(["git", "-C", top.stdout.strip(), "show", f"{c}:{path}"], capture_output=True, text=True)
        if r.returncode == 0:
            out.append((c, r.stdout))
    return out


def upgrade_base(project: Path, skill_dir: Path, name: str, explicit: str) -> tuple[str, str]:
    """The template the project's file came from: --base, else .sdlc/template-base (written by init), else the
    committed template version closest to the project's file."""
    import difflib  # noqa: PLC0415
    if explicit:
        return explicit, Path(explicit).read_text()
    rec = project / ".sdlc" / "template-base" / name
    if rec.exists():
        return str(rec.relative_to(project)), rec.read_text()
    ours = (project / name).read_text().splitlines()
    best = None
    for commit, text in template_versions(skill_dir, f"kotlin/{name}"):
        theirs = render_template(text, project).splitlines()
        changed = sum(1 for d in difflib.ndiff(ours, theirs) if d[:1] in "+-")
        if best is None or changed < best[0]:
            best = (changed, commit, render_template(text, project))
    if best is None:
        raise ValueError(f"no base for {name}: pass --base FILE (the template the project was created from)")
    return f"skill commit {best[1][:10]} (closest template, {best[0]} lines differ)", best[2]


def three_way(ours: str, base: str, theirs: str) -> tuple[str, int]:
    with tempfile.TemporaryDirectory(prefix="sdlc-upgrade-") as d:
        o, b, t = Path(d, "ours"), Path(d, "base"), Path(d, "theirs")
        o.write_text(ours)
        b.write_text(base)
        t.write_text(theirs)
        r = subprocess.run(["git", "merge-file", "-p", "-L", "project", "-L", "base", "-L", "template", str(o), str(b), str(t)],
                           capture_output=True, text=True)
        if r.returncode < 0 or r.returncode > 127:
            raise ValueError(f"git merge-file failed: {r.stderr.strip()}")
        return r.stdout, r.returncode  # exit code = number of conflicts


def cmd_upgrade(a):
    import difflib  # noqa: PLC0415
    project, skill = Path(a.project).resolve(), Path(a.skill_dir).resolve()
    tdir = skill / "templates"
    changes: dict[str, str] = {}
    bases: dict[str, str] = {}
    conflicts = []
    for name in UPGRADE_MERGED:
        if not (project / name).exists():
            continue
        theirs = render_template((tdir / "kotlin" / name).read_text(), project)
        where, base = upgrade_base(project, skill, name, a.base)
        merged, n = three_way((project / name).read_text(), base, theirs)
        print(f"{name}: base = {where}" + (f"; {n} CONFLICT(S)" if n else ""))
        if n:
            conflicts.append(name)
        changes[name] = merged
        bases[name] = theirs
    for dst, src in UPGRADE_DIRS.items():
        for f in sorted((tdir / src).rglob("*")):
            if f.is_file():
                changes[f"{dst}/{f.relative_to(tdir / src)}"] = f.read_text()
    gc = project / "gate.config"
    if gc.exists():
        have = set(re.findall(r"^(GATE_[A-Z0-9_]+)=", gc.read_text(), re.M))
        tmpl = render_template((tdir / "gate.config.kotlin").read_text(), project)
        add = [ln for ln in tmpl.splitlines() if (m := re.match(r"^(GATE_[A-Z0-9_]+)=", ln)) and m.group(1) not in have]
        if add:
            changes["gate.config"] = gc.read_text().rstrip("\n") + "\n\n# Added by sdlc.sh upgrade (keys of the current template):\n" \
                + "\n".join(add) + "\n"
    changed = []
    for rel, new in changes.items():
        old = (project / rel).read_text() if (project / rel).exists() else ""
        if old != new:
            changed.append(rel)
            sys.stdout.writelines(difflib.unified_diff(old.splitlines(True), new.splitlines(True), f"a/{rel}", f"b/{rel}"))
    if conflicts:
        print(f"upgrade: conflicts in {', '.join(conflicts)}; nothing written. Resolve by hand, or pass --base.", file=sys.stderr)
        sys.exit(1)
    if not changed:
        print("upgrade: the project already has the current harness parts")
        return
    if not a.apply:
        print(f"upgrade: {len(changed)} file(s) would change; re-run with --apply, then re-lock (sdlc.sh relock --relayed ...)")
        return
    for rel in changed:
        (project / rel).parent.mkdir(parents=True, exist_ok=True)
        (project / rel).write_text(changes[rel])
    for name, theirs in bases.items():
        rec = project / ".sdlc" / "template-base" / name
        rec.parent.mkdir(parents=True, exist_ok=True)
        rec.write_text(theirs)
    print(f"upgrade: wrote {len(changed)} file(s): {', '.join(changed)}. Locked files changed: re-lock with the user's "
          "words (sdlc.sh relock --relayed \"...\").")


def cmd_judge_check(a):
    d = Path(a.dir)
    cands = sorted(p.name.removesuffix(".md") for p in d.glob("candidate-*.md"))
    errs = []
    if not 2 <= len(cands) <= 3:
        errs.append(f"{len(cands)} candidates (need 2-3)")
    spec_ids = list(dict.fromkeys(m.group(0) for m in SPEC_ID.finditer(Path(a.spec).read_text())
                                  if m.group(0).startswith(("INV-", "FM-"))))
    if a.originals:
        orig = load(Path(a.originals))
        for c in cands:
            if sha_file(d / f"{c}.md") != orig.get(c):
                errs.append(f"{c}.md was changed by the judge")
    try:
        s = load(d / "scores.json")
        if sorted(s.get("criteria", [])) != sorted(spec_ids):
            errs.append("criteria must be exactly the spec's INV and FM IDs")
        table = s.get("candidates", {})
        if set(table) != set(cands):
            errs.append("scores.json must score every candidate")
        totals = {}
        for c in cands:
            row = table.get(c, {})
            for cid in spec_ids:
                cell = row.get(cid)
                if not (isinstance(cell, dict) and cell.get("score") in (0, 1, 2, 3) and str(cell.get("evidence", "")).strip()):
                    errs.append(f"{c} › {cid}: needs a score 0-3 and evidence")
            totals[c] = sum(row.get(cid, {}).get("score", 0) for cid in spec_ids if isinstance(row.get(cid), dict))
        if totals and s.get("winner") not in cands:
            errs.append(f"winner {s.get('winner')!r} is not a candidate")
        elif totals and totals[s["winner"]] < max(totals.values()):
            errs.append(f"winner {s['winner']} does not have the highest total {totals}")
    except (OSError, ValueError, AttributeError, TypeError, KeyError) as e:
        errs.append(f"scores.json unreadable: {e}")
    if not (d / "decision.md").exists():
        errs.append("no decision.md")
    print(json.dumps({"ok": not errs, "errors": errs[:30]}))
    sys.exit(0 if not errs else 1)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="subcommand", required=True)

    def add(subcommand, fn, *pos, **opts):
        p = sub.add_parser(subcommand)
        for x in pos:
            p.add_argument(x.lstrip("*"), nargs="*" if x.startswith("*") else None)
        for k, v in opts.items():
            flag = "--" + k.replace("_", "-")
            if v is list:
                p.add_argument(flag, dest=k, nargs="*", default=[])
            elif v is bool:
                p.add_argument(flag, dest=k, action="store_true")
            elif v is None:
                p.add_argument(flag, dest=k, required=True)
            elif isinstance(v, tuple):
                p.add_argument(flag, dest=k, type=v[0], default=v[1])
            else:
                p.add_argument(flag, dest=k, default=v)
        p.set_defaults(fn=fn)
        return p

    add("fingerprint", cmd_fingerprint, "root")
    add("copy", cmd_copy, "src", "dst", "*paths", no_docs=bool)
    add("lock", cmd_lock, "root", "lock", spec_sha="", add=list, reason="")
    add("import", cmd_import, "ws", "dst", "*files", flat=bool)
    add("spec-edit", cmd_spec_edit, run=None, how=None, note="")
    add("import-tree", cmd_import_tree, "ws", "rel", "dst")
    add("verify-lock", cmd_verify_lock, "root", "lock")
    add("ws-list", cmd_ws_list, "ws", "out")
    add("ws-changes", cmd_ws_changes, "before", "ws")
    p = sub.add_parser("run-timeout")
    p.add_argument("secs", type=int)
    p.add_argument("cmd", nargs=argparse.REMAINDER)
    p.set_defaults(fn=cmd_run_timeout)
    add("gate-step", cmd_gate_step, dir=None, root=None, name=None, mode=None, order=(int, 0), cmd="", log="", lock="",
        not_run="", exit=(int, 0), seconds=(int, 0), started=(float, 0.0), timed_out=bool, timeout=(int, 0),
        incremental=bool)
    add("gate-report", cmd_gate_report, dir=None, steps=None, label="", fp_before="", fp_after="", project_fp="",
        spec_sha="", lock_sha="", config_sha="", config_error="", scratch=bool)
    add("gate-fresh", cmd_gate_fresh, project=None, run=None)
    add("mutation-shards", cmd_mutation_shards, previous="", max=(int, 1), per_shard=(int, 150), template="",
        targets="", unit_size=(int, 10), max_threads=(int, 15), cpu_budget=(int, 0))
    add("shard-times", cmd_shard_times, plan=None, out=None, logs=list, exits=list, default_threads=(int, 1))
    add("merge-mutations", cmd_merge_mutations, "*reports", shards=None, out=None)
    add("upgrade", cmd_upgrade, project=None, skill_dir=None, base="", apply=bool)
    add("inventory-check", cmd_inventory_check, inventory=None, spec=None, out=None, junit_dir="", since=(float, 0.0))
    add("spec-gaps", cmd_spec_gaps, tests_md=None, inventory_check=None, out=None)
    add("stub-check", cmd_stub_check, "ws", "*files")
    add("add-test-deps", cmd_add_test_deps, build=None, deps=None, template='testImplementation("%s")')
    add("check-findings", cmd_check_findings, "file")
    add("validate-findings", cmd_validate_findings, project=None, workspace=None, findings=None, out=None, spec=None,
        round=(int, 1), runs=(int, 2), timeout=(int, 900))
    add("inventory-add-repros", cmd_inventory_add_repros, inventory=None, validated=None)
    add("write-record", cmd_write_record, out=None, kind=None, fingerprint="", spec_sha="", lock_sha="",
        attacker_cfg="", summary="", files=list)
    add("mutate", cmd_mutate, root=None, mutants=None)
    add("check-mutants", cmd_check_mutants, project=None, mutants=None, out=None, logs=None, timeout=(int, 900))
    add("calibrate-score", cmd_calibrate_score, cal=None, original=None, mutated=None, mutants=None, model="",
        attacker_cfg="", fingerprint="", timeout=(int, 900))
    add("escalate", cmd_escalate, project=None, run=None)
    add("report", cmd_report, project=None, run=None, attacker_cfg="")
    add("judge-check", cmd_judge_check, dir=None, spec=None, originals="")
    add("record-check", cmd_record_check, record=None, spec_sha="", attacker_cfg="")
    add("harness-sha", cmd_harness_sha)
    add("sandbox-info", cmd_sandbox_info)
    a = ap.parse_args()
    if a.subcommand == "run-timeout" and a.cmd and a.cmd[0] == "--":
        a.cmd = a.cmd[1:]
    try:
        a.fn(a)
    except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as e:
        print(f"sdlc_tool {sys.argv[1] if len(sys.argv) > 1 else ''}: error: {type(e).__name__}: {e}", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
