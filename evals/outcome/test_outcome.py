#!/usr/bin/env python3
"""Free tests for the outcome tier (CI): graders pinned both ways, verdict math, ledger tamper/stale handling.

  python3 evals/outcome/test_outcome.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import registry as R  # noqa: E402
import verdict as V  # noqa: E402

GRADED_HERE = ["property-tests", "correctness-first"]   # dst and doubt graders: evals/harness-ab/test_harness_ab.py


def _graded(task_id, which):
    t = R.TASKS[task_id]
    d = tempfile.mkdtemp(prefix="grader-")
    try:
        r = os.path.join(d, "repo")
        os.makedirs(r)
        t["setup"](r)
        if which != "untouched":
            t["write_solution"](r, which)
        return t["grade"](r, "", "", [])[0]
    finally:
        shutil.rmtree(d, ignore_errors=True)


class Graders(unittest.TestCase):
    def test_reference_solution_passes_every_primary_check(self):
        for tid in GRADED_HERE:
            checks = _graded(tid, "reference")
            failed = [c for c in R.TASKS[tid]["prereg"]["primary"] if not checks[c]]
            self.assertEqual(failed, [], f"{tid}: a correct solution must pass: {failed}")

    def test_naive_solution_fails_a_primary_check(self):
        for tid in GRADED_HERE:
            checks = _graded(tid, "naive")
            self.assertFalse(V.passed({"finished": True, "checks": checks}, R.TASKS[tid]["prereg"]["primary"]),
                             f"{tid}: a plausible weak solution must not pass: {checks}")

    def test_untouched_repo_fails(self):
        for tid in GRADED_HERE:
            checks = _graded(tid, "untouched")
            self.assertFalse(V.passed({"finished": True, "checks": checks}, R.TASKS[tid]["prereg"]["primary"]), tid)

    def test_existing_tests_pass_on_every_mutant_property_tests(self):
        # the mutants are plausible: the original example tests cannot tell them from the real code
        checks = _graded("property-tests", "untouched")
        self.assertTrue(checks["passes_real"])
        self.assertEqual([k for k, v in checks.items() if k.startswith("kills_") and v], [])

    def test_every_mutant_is_a_real_bug(self):
        """no equivalent mutants: each differs from the real code, on some input the real code round-trips"""
        import itertools
        import task_property_tests as P
        ns = {}
        for name, src in {"real": P.REAL, **P.MUTANTS}.items():
            ns[name] = {}
            exec(compile(src, name, "exec"), ns[name])
        strings = ["".join(c) for n in range(4) for c in itertools.product("a ,\"", repeat=n)]
        cases = [list(c) for n in range(3) for c in itertools.product(strings, repeat=n)] + [["x"] * 33, ["x"] * 40]
        real = ns["real"]
        for c in cases:
            self.assertEqual(real["parse_row"](real["format_row"](c)), c, c)           # the real code round-trips
        for name in P.MUT_NAMES:
            m = ns[name]
            bad = []
            for c in cases:
                try:
                    if m["parse_row"](m["format_row"](c)) != c:
                        bad.append(c)
                except Exception:
                    bad.append(c)
            self.assertTrue(bad, f"mutant {name} is equivalent to the real code on {len(cases)} cases")

    def test_correctness_first_fixed_bank_honours_the_documented_contract(self):
        """the reference bank must not fail a correct test: commit is idempotent and returns True when applied"""
        import task_correctness_first as C
        ns = {}
        exec(compile(C.BANK_FIXED, "bank", "exec"), ns)
        b = ns["Bank"]({"a": 100, "b": 0})
        p = b.prepare("a", "b", 30)
        self.assertTrue(p.commit())
        self.assertTrue(p.commit())                         # again: nothing changes
        self.assertEqual((b.balance("a"), b.balance("b")), (70, 30))
        q1, q2 = b.prepare("a", "b", 50), b.prepare("a", "b", 50)   # two outstanding, funds for one
        self.assertTrue(q1.commit())
        self.assertFalse(q2.commit())                       # refused, nothing changes
        self.assertEqual(sum(b.balance(k) for k in "ab"), 100)
        self.assertTrue(all(b.balance(k) >= 0 for k in "ab"))
        buggy = {}
        exec(compile(C.BANK, "bank", "exec"), buggy)
        bb = buggy["Bank"]({"a": 100, "b": 0, "c": 100})
        r1, r2 = bb.prepare("a", "b", 30), bb.prepare("c", "b", 20)   # both credit b from the same snapshot
        r1.commit(); r2.commit()
        self.assertNotEqual(sum(bb.balance(k) for k in "abc"), 200)   # the planted bug is still a real bug: money is lost

    def test_prereg_is_complete(self):
        for tid, t in R.TASKS.items():
            p = t["prereg"]
            self.assertGreaterEqual(p["n"], 5, tid)
            self.assertTrue(0 < p["alpha"] <= 0.05, tid)
            self.assertTrue(p["primary"], tid)
            for f in t["files"]:
                self.assertTrue(os.path.exists(f), f)

    def test_n5_can_reach_significance(self):
        # with n=5 per arm, only 5/5 vs 0/5 or 1/5, and 4/5 vs 0/5, clear alpha=0.05: say so instead of discovering it
        self.assertLessEqual(V.fisher_greater(5, 5, 1, 5), 0.05)
        self.assertLessEqual(V.fisher_greater(4, 5, 0, 5), 0.05)
        self.assertGreater(V.fisher_greater(4, 5, 1, 5), 0.05)


class Fisher(unittest.TestCase):
    def test_known_values(self):
        self.assertAlmostEqual(V.fisher_greater(5, 5, 0, 5), 1 / 252, places=6)
        self.assertAlmostEqual(V.fisher_greater(3, 3, 0, 3), 0.05, places=6)
        self.assertEqual(V.fisher_greater(0, 5, 0, 5), 1.0)
        self.assertEqual(V.fisher_greater(0, 5, 5, 5), 1.0)

    def test_monotone(self):
        for c in range(6):
            ps = [V.fisher_greater(a, 5, c, 5) for a in range(6)]
            self.assertEqual(ps, sorted(ps, reverse=True))


def _rows(task_id, passes, model='["--model","p/m"]', hashes=None):
    t = R.TASKS[task_id]
    h = hashes or V.current_hashes(task_id)
    good = {c: True for c in t["prereg"]["primary"]}
    bad = {**good, t["prereg"]["primary"][0]: False}
    out = []
    for arm, k in passes.items():
        for i in range(t["prereg"]["n"]):
            out.append({"task": task_id, "arm": arm, "i": i, "finished": True, "checks": good if i < k else bad,
                        "hashes": h, "model": model, "git_head": "x", "date": "2026-01-01"})
    return out


class Verdicts(unittest.TestCase):
    T = "correctness-first"

    def setUp(self):   # the cases below are written for 5 runs per arm, whatever the task registers
        self._n = R.TASKS[self.T]["prereg"]["n"]
        R.TASKS[self.T]["prereg"]["n"] = 5

    def tearDown(self):
        R.TASKS[self.T]["prereg"]["n"] = self._n

    def v(self, **passes):
        return V.verdict(self.T, _rows(self.T, passes))["verdict"]

    def test_verdicts(self):
        self.assertEqual(V.verdict(self.T, [])["verdict"], "NOT_RUN")
        self.assertEqual(self.v(none=0, skill=5, control=0), "WORTHWHILE")
        self.assertEqual(self.v(none=0, skill=5, control=4), "INCONCLUSIVE")     # the control does nearly as well
        self.assertEqual(self.v(none=4, skill=5, control=0), "CEILING")
        self.assertEqual(self.v(none=2, skill=2, control=0), "NOT_WORTH")
        self.assertEqual(self.v(none=0, skill=0, control=0), "FLOOR")                # nobody passes: the task shows nothing
        self.assertEqual(self.v(none=1, skill=3, control=0), "INCONCLUSIVE")
        rows = _rows(self.T, {"none": 0, "skill": 5})
        self.assertEqual(V.verdict(self.T, rows)["verdict"], "INCOMPLETE")

    def test_stale_rows_do_not_count(self):
        rows = _rows(self.T, {"none": 0, "skill": 5, "control": 0}, hashes={"skill": "old", "control": "x", "task": "y"})
        v = V.verdict(self.T, rows)
        self.assertEqual((v["verdict"], v["stale_rows"]), ("NOT_RUN", 15))

    def test_unfinished_run_fails(self):
        rows = _rows(self.T, {"none": 2, "skill": 5, "control": 0})
        for r in rows:
            if r["arm"] == "skill":
                r["finished"] = False
        self.assertEqual(V.verdict(self.T, rows)["verdict"], "NOT_WORTH")

    def test_hash_changes_when_skill_changes(self):
        h = V.skill_hash("property-tests")
        p = os.path.join(V.ROOT, "skills", "property-tests", "SKILL.md")
        orig = V._read(p)
        try:
            with open(p, "ab") as f:
                f.write(b"\n")
            self.assertNotEqual(V.skill_hash("property-tests"), h)
        finally:
            with open(p, "wb") as f:
                f.write(orig)
        self.assertEqual(V.skill_hash("property-tests"), h)


class Ledger(unittest.TestCase):
    def setUp(self):
        self.saved, self.tmp = V.LEDGER, tempfile.mkdtemp()
        V.LEDGER = self.tmp

    def tearDown(self):
        V.LEDGER = self.saved
        shutil.rmtree(self.tmp)

    def write(self, rows, name="property-tests"):
        with open(os.path.join(self.tmp, f"{name}.jsonl"), "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")

    def test_clean(self):
        self.write(_rows("property-tests", {"none": 0, "skill": 5, "control": 0}))
        self.assertEqual(V.validate_ledger(), [])

    def test_tampering_is_reported(self):
        rows = _rows("property-tests", {"none": 0, "skill": 5, "control": 0})
        rows[0]["model"] = "default"                          # model not pinned
        del rows[1]["git_head"]                              # missing field
        rows.append(dict(rows[2]))                           # duplicate run
        rows[3]["checks"] = {}                               # primary checks not graded
        self.write(rows)
        probs = " ".join(V.validate_ledger())
        for s in ("model not pinned", "missing", "duplicate run", "primary checks not graded"):
            self.assertIn(s, probs)

    def test_unknown_task_file(self):
        self.write([], name="nope")
        self.assertIn("no task", " ".join(V.validate_ledger()))

    def test_committed_ledger_is_valid(self):
        V.LEDGER = self.saved
        self.assertEqual(V.validate_ledger(), [])


class EndToEndWithFakeAgent(unittest.TestCase):
    """run.py's per-run path (session -> grade -> ledger row) with a fake `pi`: rows are valid and the verdict follows."""

    def test_rows_and_verdict(self):
        import run as RUN
        tmp = tempfile.mkdtemp()
        saved = (V.LEDGER, RUN.FULL, RUN.ARMS_DIR, os.environ.get("EVAL_PI"), os.environ.get("FAKE_TASK"))
        V.LEDGER, RUN.FULL, RUN.ARMS_DIR = os.path.join(tmp, "ledger"), os.path.join(tmp, "full"), os.path.join(tmp, "arms")
        os.makedirs(V.LEDGER); os.makedirs(RUN.FULL)
        os.environ["EVAL_PI"] = os.path.join(HERE, "fake_agent.py")
        os.environ["FAKE_TASK"] = tid = "property-tests"
        try:
            h = V.current_hashes(tid)
            for arm in V.ARMS:
                d = RUN.build_arm(R.TASKS[tid]["skill"], arm)
                for i in range(R.TASKS[tid]["prereg"]["n"]):
                    RUN.one(tid, arm, i, d, '["--model","fake/agent"]', "head", h)
            self.assertEqual(V.validate_ledger(), [])
            v = V.verdict(tid)
            n = R.TASKS[tid]["prereg"]["n"]
            self.assertEqual((v["verdict"], v["score"]["skill"], v["score"]["none"]), ("WORTHWHILE", (n, n), (0, n)))
            control = open(os.path.join(RUN.ARMS_DIR, f"{R.TASKS[tid]['skill']}-control", "SKILL.md")).read()
            real = open(os.path.join(V.ROOT, "skills", R.TASKS[tid]["skill"], "SKILL.md")).read()
            self.assertLessEqual(abs(len(control) - len(real)), 2)
            self.assertEqual(control.split("\n---", 1)[0], real.split("\n---", 1)[0])   # same name + description
        finally:
            V.LEDGER, RUN.FULL, RUN.ARMS_DIR = saved[:3]
            for k, val in (("EVAL_PI", saved[3]), ("FAKE_TASK", saved[4])):
                if val is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = val
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_run_where_the_agent_never_answers_is_not_recorded(self):
        import run as RUN
        tmp = tempfile.mkdtemp()
        saved = (V.LEDGER, RUN.FULL, os.environ.get("EVAL_PI"))
        V.LEDGER, RUN.FULL = os.path.join(tmp, "ledger"), os.path.join(tmp, "full")
        os.makedirs(V.LEDGER); os.makedirs(RUN.FULL)
        dead = os.path.join(tmp, "dead_pi.sh")
        with open(dead, "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(dead, 0o755)
        os.environ["EVAL_PI"] = dead
        try:
            with self.assertRaises(RuntimeError):
                RUN.one("property-tests", "none", 0, None, '["--model","x"]', "head", V.current_hashes("property-tests"))
            self.assertEqual(os.listdir(V.LEDGER), [])
        finally:
            V.LEDGER, RUN.FULL = saved[:2]
            if saved[2] is None:
                os.environ.pop("EVAL_PI", None)
            else:
                os.environ["EVAL_PI"] = saved[2]
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_silent_agent_times_out_instead_of_hanging(self):
        import time
        import run as RUN
        tmp = tempfile.mkdtemp()
        silent = os.path.join(tmp, "silent_pi.sh")
        with open(silent, "w") as f:
            f.write("#!/bin/sh\nsleep 60\n")
        os.chmod(silent, 0o755)
        saved = os.environ.get("EVAL_PI")
        os.environ["EVAL_PI"] = silent
        try:
            t0 = time.time()
            log, usage, finished = RUN.session(tmp, None, "hi", lambda n, t: None, turn_timeout=2)
            self.assertFalse(finished)
            self.assertLess(time.time() - t0, 20)
        finally:
            if saved is None:
                os.environ.pop("EVAL_PI", None)
            else:
                os.environ["EVAL_PI"] = saved
            shutil.rmtree(tmp, ignore_errors=True)

    def test_none_arm_prompt_does_not_name_a_missing_skill(self):
        import task_property_tests as P
        self.assertNotIn("skill", P.PROMPT_NONE.lower())
        self.assertTrue(P.PROMPT.startswith("Use the property-tests-from-examples skill."))
        self.assertTrue(P.PROMPT.endswith(P.PROMPT_NONE))      # otherwise identical

    def test_correctness_first_none_arm_prompt(self):
        import task_correctness_first as C
        self.assertNotIn("skill", C.PROMPT_NONE.lower())
        self.assertTrue(C.PROMPT.endswith(C.PROMPT_NONE) and C.PROMPT.startswith("Use the correctness-first-design skill."))

    def test_preflight_refuses_unpinned_model(self):
        import run as RUN
        saved = os.environ.pop("EVAL_PI_ARGS", None)
        try:
            with self.assertRaises(SystemExit):
                RUN.preflight(["property-tests"])
        finally:
            if saved is not None:
                os.environ["EVAL_PI_ARGS"] = saved


if __name__ == "__main__":
    unittest.main(verbosity=1)
