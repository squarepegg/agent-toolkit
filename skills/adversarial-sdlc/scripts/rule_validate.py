#!/usr/bin/env python3
"""Amendment B2: mechanical validation of a proposed ast-grep rule. No model: the orchestrator runs this.

A rule is ACCEPTED only if all of these hold (speed spec B2):
  1. it matches at least once in the pre-fix snapshot inside the lines the fix changed (diff hunks of pre vs fixed);
     a rule with no such match is vacuous and refused;
  2. it matches nothing in the fixed snapshot;
  3. every match in the current snapshot is listed; any unsuppressed match refuses the rule (a real defect becomes a
     finding, a false positive needs an `ast-grep-ignore` comment with its reason, then the rule is validated again);
  4. its rule test passes (the matched pre-fix code, on its own, still matches) and the current snapshot is scanned
     within the budget (default 5 s).
Usage: rule_validate.py --rule R.yml --pre DIR --fixed DIR [--current DIR] [--budget 5] [--sg PATH]
Exit 0 accepted, 1 refused, 2 usage/tool error. Prints one JSON object.
"""
from __future__ import annotations

import argparse
import ast
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
from pathlib import Path

LANG_EXT = {"python": ".py", "bash": ".sh", "kotlin": ".kt", "java": ".java", "typescript": ".ts",
            "javascript": ".js", "go": ".go", "rust": ".rs", "csharp": ".cs", "scala": ".scala"}


def find_sg(explicit: str | None = None) -> str | None:
    if explicit:
        return explicit
    found = shutil.which("ast-grep") or shutil.which("sg")
    if found:
        return found
    home = Path.home() / ".pi/agent/npm/node_modules/@ast-grep"
    return next((str(p) for p in sorted(home.glob("cli-*/ast-grep"))), None)


def scan(sg: str, rule: Path, root: Path) -> list[dict]:
    """Every match of `rule` under `root`, as {file (relative), line (1-based, first), end, text}."""
    if not root.exists():
        return []
    try:
        r = subprocess.run([sg, "scan", "-r", str(rule), "--json=compact", str(root)], capture_output=True, text=True)
    except OSError as e:
        raise RuntimeError(f"cannot run ast-grep ({sg}): {e}") from e
    if r.returncode not in (0, 1) or (r.stdout.strip() == "" and r.stderr.strip()):
        raise RuntimeError(f"ast-grep scan failed ({r.returncode}): {r.stderr.strip()[:300]}")
    try:
        found = json.loads(r.stdout or "[]")
    except ValueError as e:
        raise RuntimeError(f"ast-grep printed invalid JSON: {e}") from e
    out = []
    for m in found:
        rel = os.path.relpath(os.path.realpath(m["file"]), os.path.realpath(root))
        out.append({"file": rel, "line": m["range"]["start"]["line"] + 1, "end": m["range"]["end"]["line"] + 1,
                    "text": m["text"]})
    out.sort(key=lambda h: (h["file"], h["line"], h["end"], h["text"]))  # ast-grep scans in parallel: fix the order
    return out


def changed_old_lines(pre: Path, fixed: Path, rel: str) -> set[int] | None:
    """1-based line numbers of `rel` in `pre` that the fix changed or removed; None = the whole file (file gone)."""
    a, b = pre / rel, fixed / rel
    if not b.exists():
        return None
    old, new = a.read_text(errors="replace").splitlines(), b.read_text(errors="replace").splitlines()
    lines: set[int] = set()
    for tag, i1, i2, _j1, _j2 in difflib.SequenceMatcher(None, old, new, autojunk=False).get_opcodes():
        if tag == "equal":
            continue
        # a pure insertion changes no old line, but the line it lands next to is where the fix guarded the code
        lines.update(range(i1 + 1, i2 + 1) if i2 > i1 else {i1, i1 + 1})
    return lines


def rule_language(rule: Path) -> str:
    for line in rule.read_text().splitlines():
        if line.startswith("language:"):
            return line.split(":", 1)[1].strip().strip("'\"").lower()
    return ""


def enclosing_python(path: Path, line: int) -> str | None:
    """Source of the innermost function containing `line` (dedented), or None. Rules that look at their context
    (`inside: function_definition`) can only be tested on a snippet that carries it."""
    try:
        tree = ast.parse(path.read_text(errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return None
    funcs = [n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.lineno <= line <= (n.end_lineno or n.lineno)]
    if not funcs:
        return None
    best = max(funcs, key=lambda n: n.lineno)  # innermost: the one that starts last
    lines = path.read_text(errors="replace").splitlines()[best.lineno - 1:best.end_lineno]
    return textwrap.dedent("\n".join(lines))


def rule_test(sg: str, rule: Path, snippets: list[str]) -> tuple[bool, str]:
    """The matched pre-fix code, on its own (with its enclosing function, for Python), must still match: the rule does
    not rely on the rest of the file."""
    lang = rule_language(rule)
    ext = LANG_EXT.get(lang)
    if not ext:
        return True, f"language {lang or '?'}: standalone test skipped"
    with tempfile.TemporaryDirectory() as td:
        d = Path(td)
        for i, s in enumerate(snippets):
            (d / f"case{i}{ext}").write_text(s + "\n")
        hits = {m["file"] for m in scan(sg, rule, d)}
        missing = [f"case{i}" for i in range(len(snippets)) if f"case{i}{ext}" not in hits]
    return (not missing), ("rule test passes" if not missing else f"rule does not match its own pre-fix snippet(s): {missing}")


def validate(rule: Path, pre: Path, fixed: Path, current: Path | None = None, budget: float = 5.0,
             sg: str | None = None) -> dict:
    sg = find_sg(sg)
    if not sg:
        raise RuntimeError("ast-grep not found (npm i -g @ast-grep/cli): rules cannot be validated")
    res: dict = {"rule": str(rule), "accepted": False, "reason": "", "pre_hits_in_hunks": [], "fixed_hits": [],
                 "current_matches": [], "seconds": None}
    pre_hits = scan(sg, rule, pre)
    in_hunks, outside = [], []
    cache: dict[str, set[int] | None] = {}
    for h in pre_hits:
        if h["file"] not in cache:
            cache[h["file"]] = changed_old_lines(pre, fixed, h["file"])
        lines = cache[h["file"]]
        if lines is None or any(n in lines for n in range(h["line"], h["end"] + 1)):
            in_hunks.append(h)
        else:
            outside.append(h)
    res["pre_hits_in_hunks"] = [f"{h['file']}:{h['line']}" for h in in_hunks]
    res["pre_hits_outside_hunks"] = [f"{h['file']}:{h['line']}" for h in outside]
    if not in_hunks:
        res["reason"] = "vacuous: no match in the pre-fix snapshot inside the lines the fix changed"
        return res
    fixed_hits = scan(sg, rule, fixed)
    res["fixed_hits"] = [f"{h['file']}:{h['line']}" for h in fixed_hits]
    if fixed_hits:
        res["reason"] = f"still matches the fixed snapshot ({len(fixed_hits)} match(es)): the rule flags correct code"
        return res
    snippets = [(rule_language(rule) == "python" and enclosing_python(pre / h["file"], h["line"])) or h["text"]
                for h in in_hunks]
    ok, why = rule_test(sg, rule, snippets)
    res["standalone_test"] = why
    if not ok:
        res["reason"] = why
        return res
    if current is not None:
        t0 = time.monotonic()
        cur = scan(sg, rule, current)
        res["seconds"] = round(time.monotonic() - t0, 2)
        res["current_matches"] = [f"{h['file']}:{h['line']}: {h['text'].splitlines()[0][:100]}" for h in cur]
        if res["seconds"] > budget:
            res["reason"] = f"scan took {res['seconds']}s, over the {budget}s budget"
            return res
        if cur:
            res["reason"] = (f"{len(cur)} unlisted match(es) in the current snapshot: each is a defect (a finding) or "
                             "needs an `ast-grep-ignore` comment with its reason")
            return res
    res["accepted"], res["reason"] = True, "accepted"
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--rule", required=True, type=Path)
    ap.add_argument("--pre", required=True, type=Path)
    ap.add_argument("--fixed", required=True, type=Path)
    ap.add_argument("--current", type=Path)
    ap.add_argument("--budget", type=float, default=5.0)
    ap.add_argument("--sg")
    a = ap.parse_args(argv)
    for p in (a.rule, a.pre, a.fixed):
        if not p.exists():
            print(f"rule_validate: {p} does not exist", file=sys.stderr)
            return 2
    try:
        res = validate(a.rule, a.pre, a.fixed, a.current, a.budget, a.sg)
    except (RuntimeError, OSError, ValueError) as e:
        print(f"rule_validate: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2))
    return 0 if res["accepted"] else 1


if __name__ == "__main__":
    sys.exit(main())
