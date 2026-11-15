import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
def module(name):
    s=importlib.util.spec_from_file_location(name,HERE/(name+".py"));m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
F=module("futility");A=module("audit")


class Evidence(unittest.TestCase):
    def test_stopping_evidence_matches_immutable_registration_and_real_ledger(self):
        saved=json.loads((HERE/"stopping.json").read_text())
        reg=F.N.registration()
        self.assertEqual(saved["experiment"],F.N.sha(reg))
        self.assertEqual(saved["ledger_sha256"],hashlib.sha256(F.N.LEDGER.read_bytes()).hexdigest())
        self.assertEqual(saved["analysis"],F.analyze(reg,F.N.rows()))
        self.assertTrue(saved["analysis"]["all_candidates_futile"])

    def test_all_distinct_cases_covered_in_each_arm_and_model(self):
        rows=F.N.rows()
        for task in F.N.PLAN["tasks"]:
            for model in F.N.PLAN["models"]:
                for arm in F.N.PLAN["arms"]:
                    actual={r["case"] for r in rows if (r["task"],r["model"],r["arm"])==(task,model,arm)}
                    self.assertEqual(actual,{c["id"] for c in F.N.T.CASES[task]})

    def test_audit_preserves_original_checks_and_responses(self):
        ledger={r["id"]:r for r in F.N.rows() if r["task"]=="recovery"}
        records=json.loads((HERE/"handoff-responses.json").read_text())
        self.assertEqual({r["id"] for r in records},set(ledger))
        for r in records:self.assertEqual(r["checks"],ledger[r["id"]]["checks"])
        self.assertEqual(A.summarize(records)["rows"],12)

    def test_equivalent_labels_do_not_allow_missing_or_unrelated_checks(self):
        for label in ("Browser smoke","browser smoke test","  BROWSER  SMOKE TEST  "):
            self.assertTrue(A.unrun_preserved([label]))
        for wrong in (None,[],["integration"],["browser smoke","integration"],[True]):
            self.assertFalse(A.unrun_preserved(wrong))


if __name__=="__main__":unittest.main()
