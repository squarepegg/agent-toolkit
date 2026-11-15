"""Witnesses for skills/tests-as-data and skills/doubt (the parts of their advice that are checkable without a model)."""
from __future__ import annotations

import io
import os
import random
import subprocess
import tempfile
import unittest

from core import witness


# =========================================================================== tests-as-data
# --------------------------------------------------------------------------- shared state: the runner's order must not matter
def _suite_class(fresh_state: bool):
    shared = {"mode": "lenient"}

    class ConfigTest(unittest.TestCase):
        def setUp(self):
            self.cfg = {"mode": "lenient"} if fresh_state else shared

        def test_a_strict_rejects_blank(self):
            self.cfg["mode"] = "strict"
            self.assertEqual(self.cfg["mode"], "strict")

        def test_b_strict_mode_is_on(self):     # passes only because test_a ran first and leaked its state
            if fresh_state:
                self.cfg["mode"] = "strict"
            self.assertEqual(self.cfg["mode"], "strict")

        def test_c_lenient_default(self):
            if fresh_state:
                self.assertEqual(self.cfg["mode"], "lenient")
    return ConfigTest


def _run_order(cls, order):
    suite = unittest.TestSuite(cls(n) for n in order)
    r = unittest.TextTestRunner(stream=io.StringIO(), verbosity=0).run(suite)
    return r.wasSuccessful()


def _default_order(fresh):
    cls = _suite_class(fresh)
    names = unittest.TestLoader().getTestCaseNames(cls)
    ok = _run_order(cls, names)
    return (not ok, f"default (alphabetical) order {'passes' if ok else 'fails'}")


def _shuffled(fresh, runs=8):
    names = sorted(n for n in dir(_suite_class(fresh)) if n.startswith("test_"))
    rng = random.Random(1)
    for i in range(runs):
        order = rng.sample(names, len(names))
        if not _run_order(_suite_class(fresh), order):
            return True, f"shuffled run {i} fails: {order}"
    return False, f"{runs} shuffled orders pass"


witness(
    "tad-shuffle-once", "tests-as-data",
    "A suite whose tests share mutable state passes in the runner's default order; one seeded shuffled run exposes the "
    "order dependence; fresh state per case passes every order.",
    ignored=lambda: _default_order(False),
    followed=lambda: _shuffled(False),
    fixed=lambda: _shuffled(True),
)


# --------------------------------------------------------------------------- goldens: run each case twice before approving
_counter = iter(range(10**9))


def render_report(user, scrub: bool):
    req = f"req-{next(_counter)}"                          # a per-call id leaks into the output
    return f"user: {user}\nrequest: {'<id1>' if scrub else req}\nstatus: ok\n"


def _approve(scrub: bool, double_run: bool):
    first = render_report("ada", scrub)
    if double_run and render_report("ada", scrub) != first:
        return None                                        # refused: differs between two runs
    return first


def _golden_flaky(scrub: bool, double_run: bool):
    golden = _approve(scrub, double_run)
    if golden is None:
        return True, "refused to approve: two runs differ (flaky by construction)"
    later = render_report("ada", scrub)                    # the next CI run
    return (False, "approved; next run " + ("matches" if later == golden else "DIFFERS: a flaky golden was approved"))


witness(
    "tad-golden-double-run", "tests-as-data",
    "A golden that embeds a per-call id gets approved after one run and then fails the next CI run; running the case "
    "twice before approving refuses it; a printer that scrubs ids to <id1> passes the double run.",
    ignored=lambda: _golden_flaky(scrub=False, double_run=False),
    followed=lambda: _golden_flaky(scrub=False, double_run=True),
    fixed=lambda: _golden_flaky(scrub=True, double_run=True),
)


# --------------------------------------------------------------------------- check the runner against a deliberately wrong expectation
def runner_buggy(fn, rows):
    """Compares the output with itself (a typo agents make: `got == got`). Every row 'passes'."""
    return [i for i, _ in rows if fn(i) != fn(i)]


def runner_ok(fn, rows):
    return [i for i, e in rows if fn(i) != e]


SQUARE_ROWS = [(2, 4), (3, 9), (0, 0)]
WRONG_ROW = [(3, 10)]


def _runner_check(runner, with_wrong_row: bool):
    real = runner(lambda x: x * x, SQUARE_ROWS)
    if real:
        return True, f"real rows fail: {real}"
    if not with_wrong_row:
        return False, "all real rows green (runner not checked)"
    red = runner(lambda x: x * x, WRONG_ROW)
    return (not red, "wrong expectation stays GREEN: the runner never compares" if not red else "wrong expectation goes red")


witness(
    "tad-runner-wrong-expectation", "tests-as-data",
    "A runner that never really compares is green on every real row, so a green run proves nothing; one deliberately "
    "wrong expectation stays green and exposes it; a correct runner turns that row red.",
    ignored=lambda: _runner_check(runner_buggy, with_wrong_row=False),
    followed=lambda: _runner_check(runner_buggy, with_wrong_row=True),
    fixed=lambda: _runner_check(runner_ok, with_wrong_row=True),
)


# --------------------------------------------------------------------------- pin the grader both ways
PROBE = {"expect": [["conformance"], ["real"]]}          # every group needed, any-of inside a group
GOOD = "Add a conformance test that replays the same ops against the real dependency and diffs the results."
BAD = "Add a conformance test." # plausible, incomplete: misses that it must run against the real thing


def grade_buggy(probe, text):        # any group is enough (all/any swapped)
    return any(any(a in text.lower() for a in g) for g in probe["expect"])


def grade_ok(probe, text):
    return all(any(a in text.lower() for a in g) for g in probe["expect"])


def _grader_pinned(grade, known_bad: bool):
    if not grade(PROBE, GOOD):
        return True, "known-good answer fails"
    if not known_bad:
        return False, "known-good answer passes (only fixture)"
    return (grade(PROBE, BAD), "known-bad answer PASSES: grader too loose" if grade(PROBE, BAD) else "known-bad answer fails")


witness(
    "tad-grader-both-ways", "tests-as-data",
    "A grader with all/any swapped passes the known-good answer, so a good-only fixture misses the bug; a known-bad "
    "fixture passes it too and exposes it; the correct grader passes good and fails bad.",
    ignored=lambda: _grader_pinned(grade_buggy, known_bad=False),
    followed=lambda: _grader_pinned(grade_buggy, known_bad=True),
    fixed=lambda: _grader_pinned(grade_ok, known_bad=True),
)


# --------------------------------------------------------------------------- mutation shows tests catch faults, not that they are right
def vat(net_cents):                  # the code under test has a wrong rate (should be 20%)
    return net_cents * 21 // 100


def vat_ok(net_cents):
    return net_cents * 20 // 100


ORACLE_RATE = 20                      # the official doc

MUTANTS = [lambda n: n * 21 // 10, lambda n: n * 12 // 100, lambda n: n + 21, lambda n: 0]


def _rows_from(fn):                   # expected values copied from the code's own output (characterisation)
    return [(n, fn(n)) for n in (100, 250, 999, 10_000)]


def _check(fn, rows, oracle: bool):
    killed = sum(any(m(n) != e for n, e in rows) for m in MUTANTS)
    if killed < len(MUTANTS):
        return True, f"mutation: {killed}/{len(MUTANTS)} killed"
    if not oracle:
        return False, f"mutation score {killed}/{len(MUTANTS)}, rows look strong"
    wrong = [(n, e) for n, e in rows if e != n * ORACLE_RATE // 100]
    return (bool(wrong), f"oracle disagrees on {wrong}" if wrong else f"mutants {killed}/{len(MUTANTS)}, oracle agrees")


witness(
    "tad-oracle-not-mutation", "tests-as-data",
    "Rows copied from the code's own output kill every mutant (100% mutation score) while encoding a wrong VAT rate; "
    "checking the expected values against an oracle (the documented rate) catches it; correct code and rows pass both.",
    ignored=lambda: _check(vat, _rows_from(vat), oracle=False),
    followed=lambda: _check(vat, _rows_from(vat), oracle=True),
    fixed=lambda: _check(vat_ok, _rows_from(vat_ok), oracle=True),
)


# =========================================================================== doubt
ARTIFACT = 'def f(x):\n    """Returns `$(touch PWNED)` and "quoted" text."""\n    return x  # $HOME\n'
PLAIN = "def f(x):\n    return x\n"


def _send(artifact: str, via_stdin: bool):
    """Hand an artifact to a reviewer CLI (here `cat`, a stand-in that just records its input). caught = what the
    reviewer received differs from the artifact, or the artifact executed a command."""
    with tempfile.TemporaryDirectory() as d:
        if via_stdin:
            with open(os.path.join(d, "prompt.md"), "w") as f:
                f.write(artifact)
            cmd = "cat - > received.txt < prompt.md"
        else:
            cmd = f'printf "%s" "{artifact}" > received.txt'      # interpolated into a shell-quoted argument
        subprocess.run(["sh", "-c", cmd], cwd=d, capture_output=True, timeout=10)
        got = open(os.path.join(d, "received.txt")).read() if os.path.exists(os.path.join(d, "received.txt")) else ""
        executed = os.path.exists(os.path.join(d, "PWNED"))
        bad = executed or got != artifact
        return bad, ("artifact EXECUTED `touch PWNED`; " if executed else "") + ("reviewer got a mangled prompt" if got != artifact else "reviewer got the exact artifact")


witness(
    "doubt-artifact-via-stdin", "doubt",
    "Interpolating an artifact into a shell-quoted argument works on plain code (so a quick try looks fine), but an "
    "artifact with $(...), backticks or quotes executes a command and reaches the reviewer mangled; writing it to a "
    "file and piping via stdin delivers it byte-for-byte and runs nothing.",
    ignored=lambda: _send(PLAIN, via_stdin=False),
    followed=lambda: _send(ARTIFACT, via_stdin=False),
    fixed=lambda: _send(ARTIFACT, via_stdin=True),
)
