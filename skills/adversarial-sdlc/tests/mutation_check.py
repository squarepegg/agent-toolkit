#!/usr/bin/env python3
"""Mutation gate for the harness's own new Python: every listed target must have all mutants killed except the
reviewed equivalents named here (line:operator, with the reason). Slow (minutes): run before a release, or with
`selftest.sh --mutation`. Exit 0 only when no mutant survives."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
S = "scripts"
# (target, tests, extra pymutate args). Equivalent mutants are accepted by "line:operator" and explained.
TARGETS: list[tuple[str, list[str], list[str]]] = [
    (f"{S}/rule_validate.py", ["tests/rule_validate_unit_test.py", "tests/planted_recurrence_test.py"], [
        # 190: indent=2 of the printed JSON is cosmetic; 85: split(":", 1) maxsplit differs only for a value with 2+ colons
        "--accept", "190:bump int,85:bump int"]),
    (f"{S}/sdlc_tool.py", ["tests/gate_rules_unit_test.py", "tests/json_io_unit_test.py", "tests/shard_unit_test.py",
                           "tests/planted_recurrence_test.py", "tests/gate_step_rules_test.py"], [
        "--func", "gate_rules", "--func", "parse_json", "--func", "read_jsonl", "--func", "load", "--func", "lock_history",
        "--func", "class_costs",
        # 600: if the guard's `or` chain misbehaves, load(None) raises TypeError, which the next line already turns into {};
        # 611: tests.get(c, 0) - n instead of + n negates every class's sum, and costs are ratios of those sums
        "--accept", "600:and/or swap,611:arithmetic operator"]),
    (f"{S}/adopt_rules.py", ["tests/adopt_rules_unit_test.py", "tests/adopt_run_test.py", "tests/gated_list_unit_test.py"], [
        # 33: defensive importlib guard; 129: the audit log's parent (.sdlc/consolidate) always exists, because the
        # staged rules being adopted live under it
        "--accept", "33:and/or swap,129:flip bool"]),
    (f"{S}/consolidate.py", ["tests/consolidate_unit_test.py", "tests/consolidation_report_test.py"], [
        # 29: defensive importlib guard that cannot trigger for an existing file; 105: indent of the written report
        "--accept", "29:and/or swap,105:bump int"]),
]


def main() -> int:
    bad = 0
    for target, tests, extra in TARGETS:
        r = subprocess.run([sys.executable, "tests/pymutate.py", target, "--tests", *tests, *extra], cwd=ROOT,
                           capture_output=True, text=True)
        print("\n".join(ln for ln in r.stdout.splitlines() if not ln.startswith("{")))
        bad += r.returncode != 0
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
