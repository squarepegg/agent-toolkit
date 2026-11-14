#!/usr/bin/env python3
"""Tiny table + golden runner (stdlib). Copy, point TARGET at the code under test, then treat it as frozen.

Table  cases/*.csv   header row, then one stateless row per case; columns are the keyword args of TARGET plus
                     `expected` (required; a blank cell fails the parse, so no row can skip its assertion).
Golden cases/*.in    TARGET gets the file text; the result is compared with the sibling *.golden file.
Every case runs twice and must give the same output. Failures print a unified diff.
Approve new/changed goldens only by a human:  APPROVE=1 python3 table_runner.py   (refused when CI is set).
"""
import csv, difflib, glob, os, sys


def TARGET(**kw):  # replace with the real call; return a string
    raise NotImplementedError


def table_cases(path):
    with open(path, newline="", encoding="utf-8") as f:
        for n, row in enumerate(csv.DictReader(f, skipinitialspace=True), 2):
            if not (row.get("expected") or "").strip(): raise ValueError(f"{path}:{n}: blank `expected`")
            exp = row.pop("expected")
            yield f"{os.path.basename(path)}:{n}", row, exp


def check(name, got1, got2, want):
    if got1 != got2: return [f"{name}: NON-DETERMINISTIC (two runs differ)"]
    if got1 == want: return []
    d = "".join(difflib.unified_diff(want.splitlines(1), got1.splitlines(1), "expected", "actual"))
    return [f"{name}: mismatch\n{d}"]


def run(cases_dir, target=None, approve=False):
    target, fails = target or TARGET, []
    if approve and os.environ.get("CI"): raise SystemExit("refusing to approve goldens in CI")
    for p in sorted(glob.glob(os.path.join(cases_dir, "*.csv"))):
        for name, row, exp in table_cases(p):
            fails += check(name, str(target(**row)), str(target(**row)), exp)
    for p in sorted(glob.glob(os.path.join(cases_dir, "*.in"))):
        gp = p[:-3] + ".golden"
        with open(p, encoding="utf-8") as f: text = f.read()
        a, b = str(target(text)), str(target(text))
        want = ""
        if os.path.exists(gp):
            with open(gp, encoding="utf-8") as f: want = f.read()
        if approve and a == b and want != a:
            with open(gp, "w", encoding="utf-8") as f: f.write(a)
            print(f"approved {gp}"); continue
        fails += check(os.path.basename(p), a, b, want)
    return fails


if __name__ == "__main__":
    out = run(sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("-") else "cases", approve=os.environ.get("APPROVE") == "1")
    print("\n".join(out) or "all cases pass")
    sys.exit(1 if out else 0)
