#!/usr/bin/env python3
"""Unit tests for scripts/findings.py. Run: python3 findings_unit_test.py   (SCRIPTS_DIR overrides the code under test)"""
import copy, json, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.environ.get("SCRIPTS_DIR", os.path.join(HERE, "..", "scripts"))
sys.path.insert(0, SCRIPTS)
import findings  # noqa: E402


def f(id="F1", **kw):
    d = {"id": id, "axis": "standards", "severity": "major", "file": "a.py", "line": 3, "claim": "swallows the error",
         "evidence": {"kind": "code", "ref": "except: pass"}, "verdict": "confirmed"}
    d.update(kw)
    return d


def doc(*fs, runs=1, not_reviewed=("generated code",)):
    return {"runs": runs, "reviewed": ["a.py"], "not_reviewed": list(not_reviewed), "findings": list(fs)}


class Validate(unittest.TestCase):
    def test_good_doc_is_valid(self):
        self.assertEqual(findings.validate(doc(f())), [])

    def test_each_required_field_is_enforced(self):
        for k in findings.REQ:
            x = f(); del x[k]
            self.assertTrue(findings.validate(doc(x)), k)

    def test_no_evidence_no_finding(self):
        for ev in ({}, {"kind": "code"}, {"kind": "code", "ref": " "}, {"kind": "vibes", "ref": "x"},
                   {"kind": "code", "ref": "x", "extra": 1}, "x", None):
            self.assertTrue(findings.validate(doc(f(evidence=ev))), ev)

    def test_needs_validation_requires_check(self):
        self.assertTrue(findings.validate(doc(f(verdict="needs_validation"))))
        self.assertEqual(findings.validate(doc(f(verdict="needs_validation", check="run against prod copy"))), [])

    def test_rejected_requires_reason(self):
        self.assertTrue(findings.validate(doc(f(verdict="rejected"))))
        self.assertEqual(findings.validate(doc(f(verdict="rejected", reason="caller checks it at b.py:9"))), [])

    def test_enums_lines_ids_and_unknown_keys(self):
        for bad in (f(axis="style"), f(severity="critical"), f(verdict="maybe"), f(line=0), f(line=True), f(line="3"),
                    f(file=""), f(claim=""), f(bogus=1)):
            self.assertTrue(findings.validate(doc(bad)), bad)
        self.assertTrue(findings.validate(doc(f("X"), f("X"))))

    def test_not_reviewed_must_be_stated(self):
        d = doc(f()); del d["not_reviewed"]
        self.assertTrue(findings.validate(d))
        self.assertEqual(findings.validate(doc(f(), not_reviewed=())), [])

    def test_runs_must_be_positive_int(self):
        for r in (0, -1, True, "2", None):
            d = doc(f()); d["runs"] = r
            self.assertTrue(findings.validate(d), r)


class Merge(unittest.TestCase):
    def test_union_dedups_same_location_and_claim(self):
        m = findings.merge([doc(f("A")), doc(f("B"))])
        self.assertEqual(len(m["findings"]), 1)
        self.assertEqual(m["runs"], 2)

    def test_union_keeps_distinct_findings(self):
        m = findings.merge([doc(f("A")), doc(f("B", line=9, claim="other"))])
        self.assertEqual(len(m["findings"]), 2)

    def test_class_key_dedups_differently_worded_claims(self):
        m = findings.merge([doc(f("A", **{"class": "7"})), doc(f("B", claim="reworded", **{"class": "7"}))])
        self.assertEqual(len(m["findings"]), 1)

    def test_strongest_verdict_and_severity_win(self):
        a = f("A", verdict="rejected", reason="x", severity="nit")
        b = f("B", verdict="confirmed", severity="blocker")
        m = findings.merge([doc(a), doc(b)])["findings"][0]
        self.assertEqual((m["verdict"], m["severity"]), ("confirmed", "blocker"))

    def test_merge_is_order_independent_and_renumbers(self):
        x, y = doc(f("A")), doc(f("A", line=9, claim="other"))
        m1, m2 = findings.merge([x, y]), findings.merge([y, x])
        self.assertEqual(json.dumps(m1, sort_keys=True), json.dumps(m2, sort_keys=True))
        self.assertEqual([i["id"] for i in m1["findings"]], ["F001", "F002"])

    def test_inputs_not_mutated(self):
        x = doc(f("A")); before = copy.deepcopy(x)
        findings.merge([x, doc(f("B"))])
        self.assertEqual(x, before)


class Report(unittest.TestCase):
    def test_golden(self):
        d = doc(f("A", severity="minor", line=7, claim="minor thing", fix="rename"),
                f("B", severity="blocker", line=2, claim="drops data"),
                f("C", verdict="needs_validation", check="try on prod copy", claim="maybe slow"),
                f("D", verdict="rejected", reason="guarded at b.py:1", claim="null deref"), runs=2)
        want = open(os.path.join(HERE, "golden_report.txt"), encoding="utf-8").read().rstrip("\n")
        self.assertEqual(findings.report(d, "T3"), want)

    def test_blocker_means_not_merge_ready(self):
        self.assertIn("merge-ready: no", findings.report(doc(f(severity="major")), None))
        self.assertIn("merge-ready: yes", findings.report(doc(f(severity="minor")), None))
        # an unverified or refuted blocker does not block
        self.assertIn("merge-ready: yes", findings.report(doc(f(severity="blocker", verdict="needs_validation", check="c")), None))

    def test_confirmed_ordered_by_severity(self):
        r = findings.report(doc(f("A", severity="nit", line=1), f("B", severity="blocker", line=9, claim="z")), None)
        self.assertLess(r.index("[blocker]"), r.index("[nit]"))


class Cli(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, os.path.join(SCRIPTS, "findings.py"), *args], capture_output=True, text=True)

    def write(self, d, name, obj):
        p = os.path.join(d, name); json.dump(obj, open(p, "w")); return p

    def test_t3_gate_needs_two_passes(self):
        with tempfile.TemporaryDirectory() as d:
            p = self.write(d, "a.json", doc(f(), runs=1))
            self.assertEqual(self.run_cli("report", p, "--tier", "T3").returncode, 1)
            self.assertEqual(self.run_cli("report", p, "--tier", "T2").returncode, 0)
            q = self.write(d, "b.json", doc(f(), runs=2))
            self.assertEqual(self.run_cli("report", q, "--tier", "T3").returncode, 0)

    def test_merge_then_report_meets_t3(self):
        with tempfile.TemporaryDirectory() as d:
            a, b = self.write(d, "a.json", doc(f("A"))), self.write(d, "b.json", doc(f("B", line=9, claim="o")))
            out = os.path.join(d, "m.json")
            self.assertEqual(self.run_cli("merge", a, b, "-o", out).returncode, 0)
            self.assertEqual(self.run_cli("report", out, "--tier", "T3").returncode, 0)

    def test_invalid_input_fails_merge_and_writes_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            a, bad = self.write(d, "a.json", doc(f())), self.write(d, "bad.json", doc(f(evidence={})))
            out = os.path.join(d, "m.json")
            self.assertEqual(self.run_cli("merge", a, bad, "-o", out).returncode, 1)
            self.assertFalse(os.path.exists(out))

    def test_unreadable_file_exits_2(self):
        self.assertEqual(self.run_cli("validate", "/nonexistent.json").returncode, 2)


if __name__ == "__main__":
    unittest.main()
