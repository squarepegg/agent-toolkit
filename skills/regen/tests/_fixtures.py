"""Shared helpers for the regen script tests: tempdir git repos with deterministic history."""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

REGEN = Path(__file__).resolve().parent.parent
SHARED = REGEN / "_shared" / "scripts"
PY = sys.executable

_ENV = {
    "GIT_AUTHOR_NAME": "Test Author",
    "GIT_AUTHOR_EMAIL": "author@example.com",
    "GIT_COMMITTER_NAME": "Test Author",
    "GIT_COMMITTER_EMAIL": "author@example.com",
    "GIT_CONFIG_GLOBAL": os.devnull,
    "GIT_CONFIG_SYSTEM": os.devnull,
    "GIT_CONFIG_NOSYSTEM": "1",
}


def env(extra=None):
    e = dict(os.environ)
    for k in ("PHOENIX_DECISION_STRICT", "GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"):
        e.pop(k, None)
    e.update(_ENV)
    e.update(extra or {})
    return e


def git(repo, *args, check=True, extra_env=None):
    p = subprocess.run(["git", *args], cwd=str(repo), capture_output=True, text=True, env=env(extra_env))
    if check and p.returncode != 0:
        raise RuntimeError(f"git {args} failed: {p.stderr}")
    return p


def run(cmd, cwd=None, extra_env=None):
    return subprocess.run([str(c) for c in cmd], cwd=str(cwd) if cwd else None, capture_output=True,
                          text=True, env=env(extra_env))


def tempdir():
    """Resolved path (macOS /var -> /private/var) so it matches `git rev-parse --show-toplevel`."""
    return Path(tempfile.mkdtemp(prefix="regen-test-")).resolve()


def init_repo(path=None):
    path = Path(path) if path else tempdir()
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-q")
    return path


def write(repo, rel, text):
    p = Path(repo) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


_clock = [0]


def commit(repo, message, files=None):
    """Write `files` ({rel: text}), stage everything, commit with a strictly increasing date (one day apart)."""
    for rel, text in (files or {}).items():
        write(repo, rel, text)
    git(repo, "add", "-A")
    _clock[0] += 1
    d = f"{1700000000 + _clock[0] * 86400} +0000"
    git(repo, "commit", "-q", "--no-verify", "-m", message,
        extra_env={"GIT_AUTHOR_DATE": d, "GIT_COMMITTER_DATE": d})
