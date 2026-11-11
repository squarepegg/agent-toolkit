#!/usr/bin/env python3
"""Small, deterministic mutation tester for the harness's own Python (stdlib only).

For one target file it enumerates single mutations (comparison and boolean operator swaps, `not` removal, `if`
negation, constant bumps, arithmetic swaps, return-value drops), applies each on a throw-away copy of the harness and
runs the named unit-test files against it. A mutant that no test notices SURVIVES: a weak spot in the tests (or an
equivalent mutant, to be judged by a human and recorded in --accept).
Usage: pymutate.py TARGET --tests T1.py [T2.py ...] [--func NAME | --lines A-B] [--accept L1,L2] [--timeout 120]
TARGET and tests are relative to the repo root. Exit 0 only when every non-accepted mutant is killed.
"""
from __future__ import annotations

import argparse
import ast
import copy
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

SWAPS: dict[type, type] = {
    ast.Eq: ast.NotEq, ast.NotEq: ast.Eq, ast.Lt: ast.LtE, ast.LtE: ast.Lt, ast.Gt: ast.GtE, ast.GtE: ast.Gt,
    ast.In: ast.NotIn, ast.NotIn: ast.In, ast.Is: ast.IsNot, ast.IsNot: ast.Is,
    ast.And: ast.Or, ast.Or: ast.And, ast.Add: ast.Sub, ast.Sub: ast.Add, ast.Mult: ast.FloorDiv,
}


def sites(tree: ast.AST, lo: int, hi: int) -> list[tuple[int, str]]:
    """(node index in ast.walk order, description) for every mutable node whose line is in [lo, hi]."""
    out = []
    for i, n in enumerate(ast.walk(tree)):
        line = getattr(n, "lineno", None)
        if line is None or not lo <= line <= hi:
            continue
        if isinstance(n, ast.Compare) and any(type(o) in SWAPS for o in n.ops):
            out.append((i, "compare operator"))
        elif isinstance(n, ast.BoolOp):
            out.append((i, "and/or swap"))
        elif isinstance(n, ast.UnaryOp) and isinstance(n.op, ast.Not):
            out.append((i, "drop not"))
        elif isinstance(n, (ast.If, ast.While, ast.IfExp)):
            out.append((i, "negate condition"))
        elif isinstance(n, ast.Constant) and isinstance(n.value, bool):
            out.append((i, "flip bool"))
        elif isinstance(n, ast.Constant) and isinstance(n.value, int) and not isinstance(n.value, bool):
            out.append((i, "bump int"))
        elif isinstance(n, ast.BinOp) and type(n.op) in SWAPS:
            out.append((i, "arithmetic operator"))
        elif isinstance(n, ast.Return) and n.value is not None and not (
                isinstance(n.value, ast.Constant) and n.value.value is None):
            out.append((i, "return None"))
    return out


def mutate(tree: ast.AST, index: int) -> ast.AST:
    t = copy.deepcopy(tree)
    n = list(ast.walk(t))[index]
    if isinstance(n, ast.Compare):
        n.ops = [SWAPS.get(type(o), type(o))() for o in n.ops[:1]] + n.ops[1:]
    elif isinstance(n, ast.BoolOp):
        n.op = SWAPS[type(n.op)]()
    elif isinstance(n, (ast.If, ast.While, ast.IfExp)):
        n.test = ast.UnaryOp(op=ast.Not(), operand=n.test)
    elif isinstance(n, ast.Constant) and isinstance(n.value, bool):
        n.value = not n.value
    elif isinstance(n, ast.Constant) and isinstance(n.value, int):
        n.value = n.value + 1
    elif isinstance(n, ast.BinOp):
        n.op = SWAPS[type(n.op)]()
    elif isinstance(n, ast.Return):
        n.value = ast.Constant(value=None)
    return t


def drop_not(tree: ast.AST, index: int) -> ast.AST:
    t = copy.deepcopy(tree)
    nodes = list(ast.walk(t))
    target = nodes[index]
    for parent in nodes:
        for field, val in ast.iter_fields(parent):
            if val is target:
                setattr(parent, field, target.operand)  # type: ignore[attr-defined]
            elif isinstance(val, list):
                for k, item in enumerate(val):
                    if item is target:
                        val[k] = target.operand  # type: ignore[attr-defined]
    return t


def func_range(tree: ast.AST, name: str) -> tuple[int, int]:
    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name:
            return n.lineno, n.end_lineno or n.lineno
    raise SystemExit(f"function {name} not found")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("target")
    ap.add_argument("--tests", nargs="+", required=True)
    ap.add_argument("--func", action="append", default=[])
    ap.add_argument("--lines")
    ap.add_argument("--accept", default="", help="comma list of 'line:description' of reviewed equivalent mutants")
    ap.add_argument("--timeout", type=int, default=120)
    a = ap.parse_args()
    src = (ROOT / a.target).read_text()
    tree = ast.parse(src)
    ranges = [func_range(tree, f) for f in a.func] or (
        [tuple(int(x) for x in a.lines.split("-"))] if a.lines else [(1, 10**9)])  # type: ignore[list-item]
    accepted = {x.strip() for x in a.accept.split(",") if x.strip()}
    work = Path(tempfile.mkdtemp(prefix="pymutate-"))
    try:
        shutil.copytree(ROOT / "skills", work / "skills", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        shutil.copytree(ROOT / "tests", work / "tests", ignore=shutil.ignore_patterns("fixture", "fakes", "__pycache__"))
        if (ROOT / "examples").is_dir():  # real run records some tests read
            shutil.copytree(ROOT / "examples", work / "examples", ignore=shutil.ignore_patterns(
                "build", ".gradle", ".kotlin", "src", "gradle", ".git", "*.jar", "*.class", "*.zip"))
        # the unmutated copy must pass: otherwise every "kill" would be meaningless
        for t in a.tests:
            r = subprocess.run([sys.executable, t], cwd=work, capture_output=True, text=True, timeout=a.timeout)
            if r.returncode != 0 or "\nRan " not in "\n" + r.stderr:
                print(f"baseline fails: {t}\n{r.stderr[-400:]}", file=sys.stderr)
                return 2
        survivors, total, accepted_n = [], 0, 0
        seen: set[tuple[int, str]] = set()
        for lo, hi in ranges:
            for idx, desc in sites(tree, lo, hi):
                n = list(ast.walk(tree))[idx]
                key = (idx, desc)
                if key in seen:
                    continue
                seen.add(key)
                total += 1
                line = getattr(n, "lineno", 0)
                mutant = drop_not(tree, idx) if desc == "drop not" else mutate(tree, idx)
                (work / a.target).write_text(ast.unparse(ast.fix_missing_locations(mutant)) + "\n")
                killed = False
                for t in a.tests:
                    try:
                        r = subprocess.run([sys.executable, t], cwd=work, capture_output=True, timeout=a.timeout)
                    except subprocess.TimeoutExpired:
                        killed = True  # a hang is noticed
                        break
                    # A test process that exits 0 without running its tests (e.g. the mutant calls sys.exit at import)
                    # must not count as a pass: unittest always prints "Ran N tests" on stderr.
                    if r.returncode != 0 or b"\nRan " not in b"\n" + r.stderr:
                        killed = True
                        break
                if not killed and f"{line}:{desc}" in accepted:
                    accepted_n += 1
                if not killed and f"{line}:{desc}" not in accepted:
                    survivors.append(f"{a.target}:{line}: {desc}: {ast.unparse(n).splitlines()[0][:90]}")
        print(f"{a.target}: {total - len(survivors) - accepted_n}/{total} mutants killed, "
              f"{accepted_n} accepted as equivalent, {len(survivors)} survived")
        for s in survivors:
            print(f"  SURVIVED {s}")
        return 0 if not survivors else 1
    finally:
        shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
