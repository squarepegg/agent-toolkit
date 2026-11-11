"""B5: the report's Consolidation section states only what files on disk say, and is absent when nothing was
consolidated (so existing reports are unchanged)."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    if spec is None or spec.loader is None:
        raise RuntimeError(rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


c = load("consolidate", "scripts/consolidate.py")
t = load("sdlc_tool", "scripts/sdlc_tool.py")
RULE = "id: eval-call\nlanguage: python\nrule:\n  pattern: eval($X)\n"


class Section(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Path(tempfile.mkdtemp())
        self.rundir = self.p / ".sdlc"
        (self.p / "src/main").mkdir(parents=True)

    def round(self, n: int, report: dict, staged: dict[str, str] | None = None) -> None:
        d = self.rundir / f"consolidate/round-{n}"
        d.mkdir(parents=True)
        (d / "report.json").write_text(json.dumps(report))
        for name, text in (staged or {}).items():
            (d / "staged-rules").mkdir(exist_ok=True)
            (d / "staged-rules" / name).write_text(text)

    def lines(self) -> str:
        mains = [f for f in (self.p / "src/main").rglob("*") if f.is_file()]
        return "\n".join(c.report_section(self.rundir, mains))

    def test_absent_when_nothing_was_consolidated(self) -> None:
        self.assertEqual(c.report_section(self.rundir, []), [])
        self.assertEqual(t.consolidation_lines(self.p, self.rundir), [])

    def test_counts_reasons_and_what_is_not_recorded(self) -> None:
        self.round(1, {"reproduced": 3, "problems": ["F9: no proposal"], "by_kind": {"rule": 2, "behaviour": 1, "none": 0},
                       "rules_accepted": 1, "rules_refused": 1, "rows": [
                           {"finding": "F1", "kind": "rule", "rule_file": "eval-call.yml", "accepted": True, "why": "accepted"},
                           {"finding": "F2", "kind": "rule", "rule_file": "bad.yml", "accepted": False, "why": "vacuous: nothing"},
                           {"finding": "F3", "kind": "behaviour", "reason": "grammar conformance"}]},
                   {"eval-call.yml": RULE})
        out = self.lines()
        self.assertIn("Findings by class over 1 fix round(s): rule 2, behaviour 1, none 0 (3 reproduced).", out)
        self.assertIn("Rules proposed 2: accepted by the mechanical check 1, refused 1.", out)
        self.assertIn("round-1 F2 `bad.yml`: vacuous: nothing", out)
        self.assertIn("Proposals the role got wrong (1): round-1: F9: no proposal", out)
        self.assertIn("Staged 1, adopted into the locked build 0", out)
        self.assertIn("staged and not adopted: eval-call.yml", out)
        self.assertIn("Behaviour checks staged for the next run", out)
        self.assertIn("round-1 F3: grammar conformance", out)
        self.assertIn("Tokens per round: not available", out)

    def test_adoption_recurrences_and_false_positives(self) -> None:
        self.round(1, {"reproduced": 1, "problems": [], "by_kind": {"rule": 1, "behaviour": 0, "none": 0},
                       "rules_accepted": 1, "rules_refused": 0, "rows": []}, {"eval-call.yml": RULE})
        (self.rundir / "consolidate/adopted.jsonl").write_text(json.dumps({"how": "relayed by agent", "rules": {"eval-call.yml": "x"}}) + "\n")
        for i, matches in enumerate(([], ["eval-call: src/main/a.py:2"], [])):
            st = self.rundir / f"gate/g{i}/steps"
            st.mkdir(parents=True)
            (st / "05-static.json").write_text(json.dumps({"metrics": {"rule_matches": matches}}))
        (self.p / "src/main/a.py").write_text("# reason: constant\n# ast-grep-ignore: eval-call\neval('1')\n"
                                             "# ast-grep-ignore: some-other-rule\nexec('1')\n")
        out = self.lines()
        self.assertIn("adopted into the locked build 1 (by: relayed by agent); staged and not adopted: none.", out)
        self.assertIn("Recurrences caught by the gate without tokens: 1 rule match(es) in 3 static step(s): eval-call: src/main/a.py:2.", out)
        self.assertIn("Accepted false positives (`ast-grep-ignore` for these rules in main sources): 1.", out)

    def test_refused_list_is_capped_and_corrupt_files_are_skipped(self) -> None:
        rows = [{"finding": f"F{i}", "kind": "rule", "rule_file": f"r{i}.yml", "accepted": False, "why": "w"} for i in range(12)]
        self.round(1, {"reproduced": 12, "problems": [], "by_kind": {"rule": 12, "behaviour": 0, "none": 0},
                       "rules_accepted": 0, "rules_refused": 12, "rows": rows})
        (self.rundir / "consolidate/round-2").mkdir()
        (self.rundir / "consolidate/round-2/report.json").write_text("{corrupt")
        out = self.lines()
        self.assertIn("F9 `r9.yml`", out)
        self.assertNotIn("F10 `r10.yml`", out)
        self.assertIn("(+2 more in .sdlc/consolidate/)", out)
        self.assertIn("over 1 fix round(s)", out)  # the corrupt round is skipped, not fatal

    def test_report_text_is_deterministic_and_adds_only_a_new_section(self) -> None:
        self.round(1, {"reproduced": 1, "problems": [], "by_kind": {"rule": 1, "behaviour": 0, "none": 0},
                       "rules_accepted": 0, "rules_refused": 1, "rows": []})
        self.assertEqual(self.lines(), self.lines())
        self.assertTrue(self.lines().lstrip().startswith("## Consolidation"))  # a new section, no existing line changed

    def rows(self, n: int) -> list[dict]:
        return [{"finding": f"F{i}", "kind": "rule", "rule_file": f"r{i}.yml", "accepted": False, "why": "w"} for i in range(n)]

    def test_refused_list_boundary_is_exactly_ten(self) -> None:
        for n, marker in ((10, None), (11, "(+1 more in .sdlc/consolidate/)")):
            self.rundir = self.p / f".sdlc{n}"
            self.round(1, {"reproduced": n, "problems": [], "by_kind": {"rule": n}, "rules_accepted": 0, "rows": self.rows(n)})
            out = self.lines()
            self.assertIn("F9 `r9.yml`", out)
            if marker:
                self.assertIn(marker, out)
                self.assertNotIn("F10 `r10.yml`", out)
            else:
                self.assertNotIn("more in", out)

    def test_missing_keys_default_to_zero_and_long_texts_are_cut(self) -> None:
        self.round(1, {"rows": [{"finding": "F1", "kind": "behaviour", "reason": "b" * 300},
                                {"finding": "F2", "kind": "rule", "rule_file": "x.yml", "accepted": False, "why": "w" * 300}]})
        out = self.lines()
        self.assertIn("rule 0, behaviour 0, none 0 (0 reproduced)", out)
        self.assertIn("Findings by class over 1 fix round(s)", out)
        self.assertIn("accepted by the mechanical check 0, refused 1", out)
        self.assertIn("F1: " + "b" * 160 + "\n", out + "\n")
        self.assertNotIn("b" * 161, out)
        self.assertNotIn("w" * 161, out)

    def test_only_the_first_five_problems_and_recurrences_are_listed(self) -> None:
        self.round(1, {"reproduced": 1, "by_kind": {}, "rules_accepted": 0, "rows": [],
                       "problems": [f"P{i}" for i in range(7)]})
        st = self.rundir / "gate/g/steps"
        st.mkdir(parents=True)
        (st / "05-static.json").write_text(json.dumps({"metrics": {"rule_matches": [f"m{i}" for i in range(7)]}}))
        out = self.lines()
        self.assertIn("Proposals the role got wrong (7): round-1: P0; round-1: P1; round-1: P2; round-1: P3; round-1: P4\n", out + "\n")
        self.assertIn("7 rule match(es) in 1 static step(s): m0; m1; m2; m3; m4.", out)

    def test_false_positives_count_only_adopted_rule_ids_and_unadopted_are_listed(self) -> None:
        self.round(1, {"reproduced": 2, "problems": [], "by_kind": {"rule": 2}, "rules_accepted": 2, "rows": []},
                   {"a.yml": RULE.replace("eval-call", "rule-a"), "b.yml": RULE.replace("eval-call", "rule-b")})
        (self.rundir / "consolidate/adopted.jsonl").write_text(json.dumps({"how": "typed at terminal", "rules": {"a.yml": "x"}}) + "\n")
        (self.p / "src/main/a.py").write_text("# ast-grep-ignore: rule-a\nx()\n# ast-grep-ignore: other\ny()\n"
                                             "# ast-grep-ignore: rule-b, rule-a\nz()\n# ast-grep-ignore: unrelated\nw()\n")
        out = self.lines()
        self.assertIn("Staged 2, adopted into the locked build 1 (by: typed at terminal); staged and not adopted: b.yml.", out)
        self.assertIn("main sources): 2.", out)  # the rule-a line and the rule-b/rule-a line; "other" is not ours

    def session(self, name: str, *usages: dict | str) -> Path:
        d = self.rundir / "sessions" / name
        d.mkdir(parents=True)
        lines = [u if isinstance(u, str) else json.dumps({"type": "message", "message": {"usage": u}}) for u in usages]
        (d / "s.jsonl").write_text("\n".join(lines) + "\n")
        return d

    def test_session_usage_sums_what_the_provider_reported_and_skips_the_rest(self) -> None:
        d = self.session("a", {"input": 4, "output": 6, "totalTokens": 100, "cost": {"total": 0.5}},
                         {"input": 1, "output": 2, "cacheRead": 3, "cacheWrite": 4, "cost": {"total": 0.25}},  # no totalTokens
                         "{corrupt", json.dumps({"type": "session"}), json.dumps({"message": {"usage": "x"}}),
                         {"totalTokens": 7})  # no cost
        self.assertEqual(c.session_usage(d), {"tokens": 100 + 10 + 7, "cost": 0.75, "messages": 3})
        self.assertEqual(c.session_usage(self.rundir / "nope"), {"tokens": 0, "cost": 0.0, "messages": 0})

    def test_tokens_per_round_and_role_come_from_round_tagged_records(self) -> None:
        a1 = self.session("a1", {"totalTokens": 1000, "cost": {"total": 0.10}})
        a1b = self.session("a1b", {"totalTokens": 234, "cost": {"total": 0.02}})
        r1 = self.session("r1", {"totalTokens": 50, "cost": {"total": 0.01}})
        a2 = self.session("a2", {"totalTokens": 400, "cost": {"total": 0.04}})
        recs = [{"role": "attack", "round": 1, "session_dir": str(a1)}, {"role": "attack", "round": 1, "session_dir": str(a1b)},
                {"role": "referee", "round": 1, "session_dir": str(r1)}, {"role": "attack", "round": 2, "session_dir": str(a2)},
                {"role": "spec", "round": None, "session_dir": str(a1)}, {"role": "build", "round": 2, "session_dir": "/nope"}]
        (self.rundir / "roles.jsonl").write_text("\n".join(json.dumps(r) for r in recs) + "\n{bad\n")
        out = "\n".join(c.token_lines(self.rundir))
        self.assertIn("round 1: attack 1,234 ($0.12), referee 50 ($0.01)", out)
        self.assertIn("round 2: attack 400 ($0.04)", out)
        self.assertIn("(1 role run(s) had no readable transcript and are not counted)", out)
        self.assertNotIn("spec", out)  # outside any round
        self.assertLess(out.index("round 1"), out.index("round 2"))

    def test_a_corrupt_adoption_log_line_is_skipped(self) -> None:
        self.round(1, {"reproduced": 1, "problems": [], "by_kind": {}, "rules_accepted": 0, "rows": []}, {"a.yml": RULE})
        (self.rundir / "consolidate/adopted.jsonl").write_text("{bad\n" + json.dumps({"how": "h", "rules": {"a.yml": "x"}}) + "\n[1]\n")
        self.assertIn("adopted into the locked build 1 (by: h)", self.lines())

    def test_without_round_tags_it_says_so(self) -> None:
        self.assertIn("not available", "\n".join(c.token_lines(self.rundir)))
        self.rundir.mkdir(exist_ok=True)
        (self.rundir / "roles.jsonl").write_text(json.dumps({"role": "attack", "session_dir": "x"}) + "\n")
        self.assertIn("not available", "\n".join(c.token_lines(self.rundir)))


if __name__ == "__main__":
    unittest.main()
