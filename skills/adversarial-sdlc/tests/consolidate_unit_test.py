#!/usr/bin/env python3
"""consolidate.py (Amendment B1/B2, orchestrator side): proposals are checked mechanically; only B2-accepted rules are
staged; a role cannot smuggle a path, a symlink, a second proposal or a finding that was not reproduced."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("consolidate", ROOT / "scripts/consolidate.py")
assert _s is not None and _s.loader is not None
c = importlib.util.module_from_spec(_s)
_s.loader.exec_module(c)

RULE = "id: eval-call\nlanguage: python\nseverity: error\nmessage: no eval\nrule:\n  pattern: eval($X)\n"
FINDINGS = {"findings": [{"id": "F1", "status": "reproduced"}, {"id": "F2", "status": "discarded"},
                         {"id": "F3", "status": "reproduced"}]}


class Consolidate(unittest.TestCase):
    def setUp(self) -> None:
        d = Path(tempfile.mkdtemp())
        self.d, self.rules, self.stage = d, d / "rules", d / "stage"
        self.pre, self.fixed = d / "pre", d / "fixed"
        for p in (self.rules, self.pre, self.fixed):
            p.mkdir()
        (self.pre / "m.py").write_text("def f(s):\n    return eval(s)\n")
        (self.fixed / "m.py").write_text("def f(s):\n    return int(s)\n")
        (self.rules / "eval-call.yml").write_text(RULE)

    def run_c(self, proposals: object) -> dict:
        return c.consolidate(FINDINGS, proposals, self.rules, self.pre, self.fixed, self.stage)

    def good(self) -> dict:
        return {"proposals": [
            {"finding": "F1", "kind": "rule", "rule_file": "eval-call.yml", "reason": "eval on input"},
            {"finding": "F3", "kind": "none", "reason": "semantics"}]}

    def test_accepted_rule_is_staged_not_adopted(self) -> None:
        r = self.run_c(self.good())
        self.assertEqual((r["rules_accepted"], r["rules_refused"], r["problems"]), (1, 0, []))
        self.assertEqual(r["by_kind"], {"rule": 1, "behaviour": 0, "none": 1})
        self.assertTrue((self.stage / "eval-call.yml").is_file())

    def test_vacuous_rule_is_refused_and_not_staged(self) -> None:
        (self.rules / "eval-call.yml").write_text(RULE.replace("eval($X)", "exec($X)"))
        r = self.run_c(self.good())
        self.assertEqual(r["rules_refused"], 1)
        self.assertIn("vacuous", r["rows"][0]["why"])
        self.assertFalse(self.stage.exists())

    def test_missing_extra_duplicate_and_bad_proposals(self) -> None:
        p = self.good()
        p["proposals"].pop()  # F3 has no proposal
        self.assertIn("F3: no proposal", self.run_c(p)["problems"])
        p = self.good()
        p["proposals"].append({"finding": "F2", "kind": "none", "reason": "x"})  # F2 was discarded
        self.assertTrue(any("F2: not a reproduced" in x for x in self.run_c(p)["problems"]))
        p = self.good()
        p["proposals"].append({"finding": "F3", "kind": "none", "reason": "again"})
        self.assertTrue(any("F3: more than one" in x for x in self.run_c(p)["problems"]))
        p = self.good()
        p["proposals"][1]["kind"] = "magic"
        self.assertTrue(any("kind must be" in x for x in self.run_c(p)["problems"]))
        p = self.good()
        p["proposals"][1]["reason"] = " "
        self.assertTrue(any("reason is required" in x for x in self.run_c(p)["problems"]))
        self.assertTrue(self.run_c([])["problems"])  # not an object

    def test_rule_file_cannot_escape_or_be_a_symlink(self) -> None:
        for bad in ("../x.yml", "/etc/passwd", "a/b.yml", ".hidden.yml", "r.txt"):
            p = self.good()
            p["proposals"][0]["rule_file"] = bad
            self.assertTrue(any("plain *.yml" in x for x in self.run_c(p)["problems"]), bad)
        (self.d / "outside.yml").write_text(RULE)
        (self.rules / "link.yml").symlink_to(self.d / "outside.yml")
        p = self.good()
        p["proposals"][0]["rule_file"] = "link.yml"
        r = self.run_c(p)
        self.assertEqual(r["rules_refused"], 1)
        self.assertFalse(self.stage.exists())

    def test_cli(self) -> None:
        (self.d / "f.json").write_text(json.dumps(FINDINGS))
        (self.d / "p.json").write_text(json.dumps(self.good()))
        base = ["--findings", str(self.d / "f.json"), "--proposals", str(self.d / "p.json"), "--rules-dir", str(self.rules),
                "--pre", str(self.pre), "--fixed", str(self.fixed), "--out", str(self.d / "r.json"),
                "--stage", str(self.stage)]
        self.assertEqual(c.main(base), 0)
        self.assertEqual(json.loads((self.d / "r.json").read_text())["rules_accepted"], 1)
        (self.d / "p.json").write_text("{bad")
        self.assertEqual(c.main(base), 2)

    def test_malformed_items_are_reported_not_raised(self) -> None:
        r = self.run_c({"proposals": [5, {"x": 1}, {"finding": 3}, *self.good()["proposals"]]})
        self.assertEqual(sum(1 for x in r["problems"] if x.startswith("malformed proposal")), 3)
        long = self.run_c({"proposals": [{"finding": 7, "pad": "p" * 200}]})["problems"][0]
        self.assertEqual(len(long), len("malformed proposal: ") + 80)
        self.assertEqual(r["rules_accepted"], 1)  # the well-formed ones still count

    def test_exact_problem_lists_and_counts(self) -> None:
        p = self.good()
        p["proposals"][1]["kind"] = "magic"
        # F3 has a proposal that is wrong: exactly that one problem, not also "no proposal"
        self.assertEqual(self.run_c(p)["problems"], ["F3: kind must be one of ('rule', 'behaviour', 'none')"])
        (self.rules / "vac.yml").write_text(RULE.replace("eval($X)", "exec($X)"))
        r = self.run_c({"proposals": [
            {"finding": "F1", "kind": "rule", "rule_file": "vac.yml", "reason": "r"},
            {"finding": "F3", "kind": "behaviour", "reason": "grammar"}]})
        self.assertEqual((r["by_kind"], r["rules_accepted"], r["rules_refused"]),
                         ({"rule": 1, "behaviour": 1, "none": 0}, 0, 1))

    def test_two_rules_are_staged_into_a_nested_new_directory(self) -> None:
        (self.rules / "second.yml").write_text(RULE.replace("eval-call", "second"))
        self.stage = self.d / "a" / "b" / "stage"
        r = self.run_c({"proposals": [
            {"finding": "F1", "kind": "rule", "rule_file": "eval-call.yml", "reason": "r"},
            {"finding": "F3", "kind": "rule", "rule_file": "second.yml", "reason": "r"}]})
        self.assertEqual(r["rules_accepted"], 2)
        self.assertEqual(sorted(x.name for x in self.stage.iterdir()), ["eval-call.yml", "second.yml"])

    def test_reason_is_cut_at_400_and_findings_json_is_not_mutated(self) -> None:
        p = self.good()
        p["proposals"][1]["reason"] = "r" * 500
        before = json.dumps(FINDINGS, sort_keys=True)
        r = self.run_c(p)
        self.assertEqual(len(next(x for x in r["rows"] if x["finding"] == "F3")["reason"]), 400)
        self.assertEqual(json.dumps(FINDINGS, sort_keys=True), before)

    def test_every_option_is_required_and_output_is_deterministic(self) -> None:
        (self.d / "f.json").write_text(json.dumps(FINDINGS))
        (self.d / "p.json").write_text(json.dumps(self.good()))
        full = {"--findings": str(self.d / "f.json"), "--proposals": str(self.d / "p.json"),
                "--rules-dir": str(self.rules), "--pre": str(self.pre), "--fixed": str(self.fixed),
                "--out": str(self.d / "r.json"), "--stage": str(self.stage)}
        for omit in full:
            argv = [x for k, v in full.items() if k != omit for x in (k, v)]
            with self.assertRaises(SystemExit) as cm:
                c.main(argv)
            self.assertEqual(cm.exception.code, 2, omit)
        argv = [x for k, v in full.items() for x in (k, v)]
        c.main(argv)
        first = (self.d / "r.json").read_text()
        c.main(argv)
        self.assertEqual((self.d / "r.json").read_text(), first)
        self.assertTrue(first.endswith("}\n"))


if __name__ == "__main__":
    unittest.main()
