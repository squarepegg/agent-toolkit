#!/usr/bin/env python3
"""Fail when a diff touches files the task type does not allow (stdlib only).

  guard.py --task feature|refactor|coverage|dsl [--base REF] [--config .tests-as-data.json] [--files a b ...]

Config (repo root): {"data": ["tests/data/**"], "golden": ["tests/golden/**"], "runner": ["tests/runner/**"]}
Anything not matched is production code. Files come from `git diff --name-only REF` (default: stdin lines) or --files.
Exit 0 ok, 1 violation(s), 2 usage/config error.
"""
import json, re, subprocess, sys

ALLOWED = {  # task -> classes that may change
    "feature": {"production", "data", "golden"},
    "refactor": {"production"},
    "coverage": {"data", "golden"},
    "dsl": {"data", "golden", "runner"},
}
ORDER = ("runner", "golden", "data")  # first match wins: the most protected class


def glob_re(g):
    out, i = "", 0
    while i < len(g):
        if g.startswith("**/", i): out += "(?:.*/)?"; i += 3
        elif g.startswith("**", i): out += ".*"; i += 2
        elif g[i] == "*": out += "[^/]*"; i += 1
        elif g[i] == "?": out += "[^/]"; i += 1
        else: out += re.escape(g[i]); i += 1
    return re.compile("^" + out + "$")


def classify(path, cfg):
    for cls in ORDER:
        if any(glob_re(g).match(path) for g in cfg.get(cls, [])): return cls
    return "production"


def violations(task, files, cfg):
    return [(f, c) for f in files for c in [classify(f, cfg)] if c not in ALLOWED[task]]


def main(argv):
    def opt(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    task = opt("--task")
    if task not in ALLOWED: print(__doc__); return 2
    try: cfg = json.load(open(opt("--config", ".tests-as-data.json")))
    except (OSError, ValueError) as e: print(f"guard: cannot read config: {e}"); return 2
    if "--files" in argv:
        files = [a for a in argv[argv.index("--files") + 1:] if not a.startswith("--")]
    elif opt("--base"):
        r = subprocess.run(["git", "diff", "--name-only", opt("--base")], capture_output=True, text=True)
        if r.returncode: print("guard: git diff failed: " + r.stderr.strip()); return 2
        files = r.stdout.split()
    else:
        files = sys.stdin.read().split()
    bad = violations(task, files, cfg)
    for f, c in bad: print(f"FAIL {f}: {c} files may not change in a {task} task")
    print(f"guard: task={task} files={len(files)} violations={len(bad)}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
