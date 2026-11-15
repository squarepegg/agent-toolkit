"""Outcome task: does skills/property-tests make an agent write tests that catch plausible wrong implementations?

The agent improves weak example tests for merge_intervals. Grading reruns the agent's tests against the real code and
against four plausible wrong implementations (mutants). No model grades anything.
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "harness-ab"))
from tasks import init_repo, unittest_outcomes  # noqa: E402

# Pre-registered (changing any of this changes the task hash and stales old ledger rows)
PREREG = {"n": 5, "alpha": 0.05, "ceiling": 0.8,
          "primary": ["passes_real", "kills_touching", "kills_unsorted", "kills_nested", "kills_lookup", "src_unchanged"]}

REAL = '''"""Interval merging."""


def merge_intervals(intervals):
    """Merge overlapping or touching closed integer intervals.

    intervals: list of (start, end) with start <= end, in any order. Returns a new sorted list of disjoint intervals
    covering exactly the same points. Touching intervals merge: [(1, 2), (2, 3)] -> [(1, 3)].
    """
    out = []
    for s, e in sorted(intervals):
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out
'''

MUTANTS = {
    "touching": REAL.replace("if out and s <= out[-1][1]:", "if out and s < out[-1][1]:"),
    "unsorted": REAL.replace("for s, e in sorted(intervals):", "for s, e in intervals:"),
    "nested": REAL.replace("out[-1] = (out[-1][0], max(out[-1][1], e))", "out[-1] = (out[-1][0], e)"),
    "lookup": REAL.replace("    out = []\n", "    known = {((1, 3), (2, 6), (8, 10)): [(1, 6), (8, 10)], ((1, 4), (5, 6)): [(1, 4), (5, 6)]}\n"
                                             "    return known.get(tuple(intervals), sorted(intervals))\n    out = []\n"),
}
assert all(m != REAL for m in MUTANTS.values())

TEST_EXISTING = '''import unittest
from intervals import merge_intervals


class MergeTest(unittest.TestCase):
    def test_overlap(self):
        self.assertEqual(merge_intervals([(1, 3), (2, 6), (8, 10)]), [(1, 6), (8, 10)])

    def test_disjoint(self):
        self.assertEqual(merge_intervals([(1, 4), (5, 6)]), [(1, 4), (5, 6)])


if __name__ == "__main__":
    unittest.main()
'''

PROMPT = ("Are the tests in tests/test_intervals.py good enough? Improve them. This is a test-only task: do not change "
          "intervals.py. No third-party packages are installed; tests run with "
          "`python3 -m unittest discover -s tests -t .`. Tell me what you found.")


def setup(r):
    init_repo(r, {"intervals.py": REAL, "tests/__init__.py": "", "tests/test_intervals.py": TEST_EXISTING,
                  "README.md": "intervals. Tests: python3 -m unittest discover -s tests -t .\n"})


def user(turn, text):
    if turn >= 3 or not re.search(r"\?|permission|approve|confirm|let me know|your call", text, re.I):
        return None
    return "Go with your recommendation and finish without asking me again."


def _outcomes_with(r, src):
    d = tempfile.mkdtemp(prefix="pt-grade-")
    try:
        shutil.copytree(os.path.join(r, "tests"), os.path.join(d, "tests"))
        with open(os.path.join(d, "intervals.py"), "w") as f:
            f.write(src)
        return unittest_outcomes(d, timeout=300)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _green(out):
    return bool(out) and all(v in ("ok", "skipped", "expected") for v in out.values())


def grade(r, final_text, all_text, bash_cmds):
    real = _outcomes_with(r, REAL)
    checks = {"passes_real": _green(real)}
    detail = {"real": real}
    for name, src in MUTANTS.items():
        o = _outcomes_with(r, src)
        checks[f"kills_{name}"] = not _green(o)
        detail[name] = {k: v for k, v in o.items() if v != "ok"}
    checks["src_unchanged"] = open(os.path.join(r, "intervals.py")).read() == REAL
    checks["names_dumb_implementation"] = bool(re.search(r"hard.?cod|dumb|lookup|cheat", all_text, re.I))
    return checks, detail


# --------------------------------------------------------------------------- grader fixtures (CI pins the grader both ways)
REFERENCE_TESTS = '''import random
import unittest
from intervals import merge_intervals


def points(iv):
    return {p for s, e in iv for p in range(s, e + 1)}


class MergeProps(unittest.TestCase):
    def test_properties(self):
        rng = random.Random(3)
        for _ in range(500):
            iv = []
            for _ in range(rng.randint(0, 6)):
                s = rng.randint(0, 20)
                iv.append((s, s + rng.randint(0, 6)))
            out = merge_intervals(list(iv))
            self.assertEqual(points(out), points(iv), iv)              # same points
            self.assertEqual(out, sorted(out), iv)                      # sorted
            for (a, b), (c, d) in zip(out, out[1:]):
                self.assertGreater(c, b, iv)                            # disjoint, not touching (touching merges)
            self.assertEqual(merge_intervals(out), out, iv)             # idempotent


if __name__ == "__main__":
    unittest.main()
'''

NAIVE_TESTS = TEST_EXISTING.replace("    def test_disjoint", "    def test_single(self):\n        self.assertEqual(merge_intervals([(2, 5)]), [(2, 5)])\n\n    def test_disjoint")


def write_solution(r, which):
    with open(os.path.join(r, "tests", "test_intervals.py"), "w") as f:
        f.write(REFERENCE_TESTS if which == "reference" else NAIVE_TESTS)


TASK = {"skill": "property-tests", "prompt": PROMPT, "setup": setup, "user": user, "grade": grade, "turn_timeout": 900,
        "prereg": PREREG, "files": [__file__], "write_solution": write_solution}
