#!/usr/bin/env python3
"""Checks the planted-defect corpus itself and the scorer. Run: python3 corpus_unit_test.py"""
import json, os, re, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.environ.get("SCRIPTS_DIR", os.path.join(HERE, "..", "scripts")))
import score_findings, findings, triage  # noqa: E402

EXP = json.load(open(os.path.join(HERE, "corpus", "expected.json")))
DIFF = open(os.path.join(HERE, "corpus", "change.diff")).read()
ADDED = [l[1:] for l in DIFF.splitlines() if l.startswith("+") and not l.startswith("+++")]


def doc(*locs, verdict="confirmed"):
    fs = [{"id": f"F{i}", "axis": "standards", "severity": "major", "file": fl, "line": ln, "claim": f"c{i}",
           "evidence": {"kind": "code", "ref": "r"}, "verdict": verdict} for i, (fl, ln) in enumerate(locs)]
    return {"runs": 1, "reviewed": ["x"], "not_reviewed": [], "findings": fs}


class Corpus(unittest.TestCase):
    def test_planted_lines_hold_the_defect(self):
        # new-file line N is ADDED[N-1] (the diff adds the whole file)
        marks = {"P1": "limit=PAGE", "P2": "except Exception", "P3": "os.environ.get"}
        for p in EXP["planted"]:
            self.assertTrue(any(marks[p["id"]] in ADDED[n - 1] for n in p["lines"]), p["id"])

    def test_decoy_is_clean_code(self):
        self.assertTrue(any("sum(xs)" in ADDED[n - 1] for n in EXP["decoys"][0]["lines"]))

    def test_corpus_triages_as_t2_or_t1_with_swallowed_error(self):
        r = triage.triage(triage.parse_diff(DIFF), triage.load_rules(os.path.join(HERE, "..", "reference", "risk-rules.json")))
        self.assertIn("error-swallow", {m["rule"] for m in r["matches"]})
        self.assertEqual(r["tier"], "T2")


class Scorer(unittest.TestCase):
    def test_perfect(self):
        d = doc(("src/export.py", 7), ("src/export.py", 13), ("src/export.py", 18))
        self.assertEqual(findings.validate(d), [])
        r = score_findings.score(d, EXP)
        self.assertEqual((r["recall"], r["decoys_hit"], r["missed"]), (1.0, [], []))

    def test_empty_misses_everything(self):
        r = score_findings.score(doc(), EXP)
        self.assertEqual((r["recall"], r["missed"]), (0.0, ["P1", "P2", "P3"]))

    def test_decoy_confirmed_is_a_false_positive(self):
        self.assertEqual(score_findings.score(doc(("src/export.py", 22)), EXP)["decoys_hit"], ["D1"])

    def test_only_confirmed_counts(self):
        self.assertEqual(score_findings.score(doc(("src/export.py", 7), verdict="rejected"), EXP)["recall"], 0.0)

    def test_wrong_file_does_not_count(self):
        self.assertEqual(score_findings.score(doc(("other.py", 7)), EXP)["recall"], 0.0)


if __name__ == "__main__":
    unittest.main()
