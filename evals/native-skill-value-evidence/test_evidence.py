"""Offline integrity checks on real model artifacts and recomputed results; no inference calls."""
import hashlib
import importlib.util
import json
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location("retained_skill_study",HERE.parent/"native-skill-value/study.py");S=importlib.util.module_from_spec(s);s.loader.exec_module(S)


class Evidence(unittest.TestCase):
    def test_retained_data_checksums(self):
        for rel,sha in json.loads((HERE/"checksums.json").read_text()).items():
            self.assertEqual(hashlib.sha256((S.ROOT/rel).read_bytes()).hexdigest(),sha,rel)

    def test_saved_summary_matches_real_ledger_and_registered_stopping_rule(self):
        reg=S.registration();summary=json.loads((HERE/"summary.json").read_text());rows=S.rows()
        self.assertEqual(summary["experiment"],S.digest(reg));self.assertEqual(len(rows),42)
        self.assertEqual(len({r["id"] for r in rows}),42)
        actual=[S.cell(t,m,rows) for t in S.PLAN["tasks"] for m in S.PLAN["models"]]
        self.assertEqual(summary["cells"],actual)
        self.assertTrue(all(c["verdict"] not in ("NOT_RUN","INCOMPLETE","INVALID_LEDGER","EXPOSURE_UNVERIFIED") for c in actual))
        self.assertEqual(summary["reference_failures"],sum(not r["checks"]["passes_real"] for r in rows))
        self.assertEqual(summary["final_source_changes"],sum(not r["checks"]["src_unchanged"] for r in rows))

    def test_every_record_has_the_exact_generated_test_bytes(self):
        rows={r["id"]:r for r in S.rows()};bundles=json.loads((HERE/"artifacts.json").read_text())
        self.assertEqual({b["id"] for b in bundles},set(rows))
        for bundle in bundles:
            size=sum(len(text.encode()) for name,text in bundle["files"].items() if name.endswith(".py"))
            self.assertEqual(size,rows[bundle["id"]]["test_bytes"],bundle["id"])
            for key in ("task","model","arm","i"):self.assertEqual(bundle[key],rows[bundle["id"]][key])


if __name__=="__main__":unittest.main()
