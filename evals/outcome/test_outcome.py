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
    T = "property-tests"

    def v(self, **passes):
        return V.verdict(self.T, _rows(self.T, passes))["verdict"]

    def test_verdicts(self):
        self.assertEqual(V.verdict(self.T, [])["verdict"], "NOT_RUN")
        self.assertEqual(self.v(none=0, skill=5, control=0), "WORTHWHILE")
        self.assertEqual(self.v(none=0, skill=5, control=4), "INCONCLUSIVE")     # the control does nearly as well
        self.assertEqual(self.v(none=4, skill=5, control=0), "CEILING")
        self.assertEqual(self.v(none=2, skill=2, control=0), "NOT_WORTH")
        self.assertEqual(self.v(none=1, skill=3, control=0), "INCONCLUSIVE")
        rows = _rows(self.T, {"none": 0, "skill": 5})
        self.assertEqual(V.verdict(self.T, rows)["verdict"], "INCOMPLETE")

    def test_stale_rows_do_not_count(self):
        rows = _rows(self.T, {"none": 0, "skill": 5, "control": 0}, hashes={"skill": "old", "control": "x", "task": "y"})
        v = V.verdict(self.T, rows)
        self.assertEqual((v["verdict"], v["stale_rows"]), ("NOT_RUN", 15))

    def test_unfinished_run_fails(self):
        rows = _rows(self.T, {"none": 0, "skill": 5, "control": 0})
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
            self.assertEqual((v["verdict"], v["score"]["skill"], v["score"]["none"]), ("WORTHWHILE", (5, 5), (0, 5)))
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
