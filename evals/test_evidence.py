#!/usr/bin/env python3
"""Tests of the free evidence tier itself (CI): the witness runner, the claims check and STATUS.md cannot be fooled.

  python3 evals/test_evidence.py
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "witness"))
import check_claims  # noqa: E402
import core  # noqa: E402
import status  # noqa: E402


class WitnessRunner(unittest.TestCase):
    def test_arm_that_contradicts_its_claim_fails(self):
        w = core.Witness("t", "s", "x", [core.Arm("ignored", False, lambda: (True, "caught anyway")),
                                         core.Arm("followed", True, lambda: (True, "")),
                                         core.Arm("fixed", False, lambda: (False, ""))])
        self.assertEqual([r.ok for r in core.run_witness(w)], [False, True, True])

    def test_crash_is_neither_catch_nor_miss(self):
        def boom():
            raise KeyError("x")
        r = core.run_arm(core.Arm("fixed", False, boom))
        self.assertIsNone(r.caught)
        self.assertFalse(r.ok)

    def test_non_bool_result_is_a_crash(self):
        self.assertFalse(core.run_arm(core.Arm("fixed", False, lambda: (0, ""))).ok)

    def test_a_different_failure_is_not_a_catch(self):
        def other():
            raise AssertionError("something unrelated")
        with self.assertRaises(RuntimeError):
            core.catches(other, "[Intended]")

    def test_registration_needs_all_three_arms(self):
        with self.assertRaises(ValueError):
            core.witness("t-missing-fixed", "s", "x", ignored=lambda: (False, ""), followed=lambda: (True, ""))
        with self.assertRaises(ValueError):
            core.witness("t-bad-name", "s", "x", ignored=lambda: (False, ""), followed=lambda: (True, ""),
                         fixed=lambda: (False, ""), maybe=lambda: (False, ""))

    def test_every_witness_is_deterministic_and_passes(self):
        reg = core.load_all()
        for w in reg.values():
            a = [(r.caught, r.detail) for r in core.run_witness(w)]
            b = [(r.caught, r.detail) for r in core.run_witness(w)]
            self.assertEqual(a, b, f"{w.id} differs between two runs")

    def test_sim_env_does_not_leak_into_witnesses(self):
        os.environ["SIM_SEED"] = "1"
        try:
            res = core.run_witness(core.load_all()["dst-reentrant-hook"])
        finally:
            del os.environ["SIM_SEED"]
        self.assertTrue(all(r.ok for r in res))


class ClaimsCheck(unittest.TestCase):
    def setUp(self):
        self.saved = check_claims.CLAIMS_DIR
        self.tmp = tempfile.mkdtemp()
        shutil.copytree(self.saved, os.path.join(self.tmp, "claims"))
        check_claims.CLAIMS_DIR = os.path.join(self.tmp, "claims")

    def tearDown(self):
        check_claims.CLAIMS_DIR = self.saved
        shutil.rmtree(self.tmp)

    def mutate(self, skill, fn):
        p = os.path.join(check_claims.CLAIMS_DIR, f"{skill}.json")
        doc = json.load(open(p))
        fn(doc["claims"])
        json.dump(doc, open(p, "w"))
        return " ".join(check_claims.check()[0])

    def test_repo_is_clean(self):
        self.assertEqual(check_claims.check()[0], [])

    def test_planted_bugs_are_caught(self):
        cases = {
            "quote not found": ("dst", lambda cs: cs[0].update(quote="Production classes are optional.")),
            "no witness": ("dst", lambda cs: cs[0].update(by=["nope"])),
            "does not exist": ("tests-as-data", lambda cs: next(c for c in cs if c["kind"] == "tool").update(by=["skills/x/missing.py"])),
            "not run by any workflow": ("tests-as-data", lambda cs: next(c for c in cs if c["kind"] == "tool").update(by=["evals/witness/core.py"])),
            "no outcome task": ("dst", lambda cs: next(c for c in cs if c["kind"] == "model").update(by=["nope"])),
            "needs a why": ("dst", lambda cs: next(c for c in cs if c["kind"] == "policy").pop("why")),
            "Hard rules with no claim": ("dst", lambda cs: [c.pop("rule", None) for c in cs if c.get("rule") == 4]),
            "cited by no claim": ("dst", lambda cs: [cs.remove(c) for c in [c for c in cs if c.get("by") == ["dst-replay-check"]]]),
            "unknown keys": ("dst", lambda cs: cs[0].update(proven=True)),
            "duplicate id": ("dst", lambda cs: cs.append(copy.deepcopy(cs[0]))),
            "kind": ("dst", lambda cs: cs[0].update(kind="vibes")),
        }
        for want, (skill, fn) in cases.items():
            self.tearDown(); self.setUp()
            self.assertIn(want, self.mutate(skill, fn), want)

    def test_missing_claims_file(self):
        os.remove(os.path.join(check_claims.CLAIMS_DIR, "doubt.json"))
        self.assertIn("no evals/claims/doubt.json", " ".join(check_claims.check()[0]))


class Status(unittest.TestCase):
    def test_committed_status_is_current(self):
        self.assertEqual(open(status.OUT).read(), status.render())


if __name__ == "__main__":
    unittest.main(verbosity=1)
