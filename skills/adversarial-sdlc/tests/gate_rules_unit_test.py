#!/usr/bin/env python3
"""GATE_RULES (Amendment B3): project ast-grep rules run in the gate's static step; a match, a missing rule file, a
missing ast-grep or a rules dir under .sdlc (not locked) each fail the step."""
from __future__ import annotations

import importlib.util
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sdlc_tool", ROOT / "scripts/sdlc_tool.py")
assert _spec is not None and _spec.loader is not None
t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t)

_v = importlib.util.spec_from_file_location("rule_validate", ROOT / "scripts/rule_validate.py")
assert _v is not None and _v.loader is not None
rv = importlib.util.module_from_spec(_v)
_v.loader.exec_module(rv)
SG = rv.find_sg() or ""

RULE = "id: eval-call\nlanguage: python\nseverity: error\nmessage: no eval\nrule:\n  pattern: eval($X)\n"


class GateRules(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "src/main").mkdir(parents=True)
        (self.root / "rules").mkdir()
        (self.root / "rules/eval-call.yml").write_text(RULE)
        (self.root / "src/main/a.py").write_text("x = int('1')\n")

    def run_rules(self, rules: str = "rules") -> list[tuple[bool, str]]:
        out: list[tuple[bool, str]] = []
        env = {"GATE_RULES": rules, "GATE_MAIN_PATHS": "src/main", "GATE_AST_GREP": SG}
        with mock.patch.dict(os.environ, env):
            t.gate_rules(self.root, lambda ok, what: out.append((bool(ok), what)), {})
        return out

    def test_clean_sources_pass(self) -> None:
        self.assertTrue(all(ok for ok, _ in self.run_rules()), self.run_rules())

    def test_a_match_fails_and_names_the_line(self) -> None:
        (self.root / "src/main/a.py").write_text("x = 1\ny = eval('1')\n")
        res = self.run_rules()
        self.assertFalse(res[-1][0])
        metrics: dict = {}
        with mock.patch.dict(os.environ, {"GATE_RULES": "rules", "GATE_MAIN_PATHS": "src/main", "GATE_AST_GREP": SG}):
            t.gate_rules(self.root, lambda ok, what: None, metrics)
        self.assertEqual(metrics["rule_matches"], ["eval-call: src/main/a.py:2"])

    def test_no_rule_files_fails(self) -> None:
        (self.root / "rules/eval-call.yml").unlink()
        self.assertFalse(all(ok for ok, _ in self.run_rules()))

    def test_rules_under_dot_sdlc_or_escaping_fail(self) -> None:
        for bad in (".sdlc/rules", "../rules", "/etc"):
            self.assertFalse(self.run_rules(bad)[0][0], bad)

    def test_missing_ast_grep_fails(self) -> None:
        with mock.patch.object(t.shutil, "which", return_value=None), mock.patch.dict(os.environ, {"GATE_AST_GREP": ""}):
            res = []
            env = {"GATE_RULES": "rules", "GATE_MAIN_PATHS": "src/main", "GATE_AST_GREP": ""}
            with mock.patch.dict(os.environ, env):
                t.gate_rules(self.root, lambda ok, what: res.append((bool(ok), what)), {})
        self.assertFalse(all(ok for ok, _ in res))
        self.assertTrue(any("ast-grep found" in w and not ok for ok, w in res))

    def test_ignore_comment_suppresses(self) -> None:
        (self.root / "src/main/a.py").write_text("# reason: constant\n# ast-grep-ignore: eval-call\ny = eval('1')\n")
        self.assertTrue(all(ok for ok, _ in self.run_rules()))

    def test_listed_matches_are_capped_at_twenty_but_all_count(self) -> None:
        (self.root / "src/main/a.py").write_text("".join(f"eval({i})\n" for i in range(25)))
        metrics: dict = {}
        out: list[tuple[bool, str]] = []
        with mock.patch.dict(os.environ, {"GATE_RULES": "rules", "GATE_MAIN_PATHS": "src/main", "GATE_AST_GREP": SG}):
            t.gate_rules(self.root, lambda ok, what: out.append((bool(ok), what)), metrics)
        self.assertEqual(len(metrics["rule_matches"]), 20)
        self.assertIn("(25 matches)", out[-1][1])
        self.assertFalse(out[-1][0])

    def test_missing_main_path_is_skipped_and_ast_grep_errors_fail(self) -> None:
        with mock.patch.dict(os.environ, {"GATE_RULES": "rules", "GATE_MAIN_PATHS": "src/main nowhere", "GATE_AST_GREP": SG}):
            out: list[tuple[bool, str]] = []
            t.gate_rules(self.root, lambda ok, what: out.append((bool(ok), what)), {})
        self.assertTrue(all(ok for ok, _ in out), out)
        (self.root / "rules/broken.yml").write_text("id: x\nlanguage: nope\nrule:\n  pattern: [\n")
        metrics: dict = {}
        with mock.patch.dict(os.environ, {"GATE_RULES": "rules", "GATE_MAIN_PATHS": "src/main", "GATE_AST_GREP": SG}):
            out = []
            t.gate_rules(self.root, lambda ok, what: out.append((bool(ok), what)), metrics)
        self.assertFalse(out[-1][0])
        self.assertTrue(any("broken.yml: ast-grep failed" in m for m in metrics["rule_matches"]), metrics)

    def test_the_binary_is_found_on_path_as_ast_grep_or_as_sg(self) -> None:
        for name in ("ast-grep", "sg"):
            bin_dir = Path(tempfile.mkdtemp())
            (bin_dir / name).symlink_to(SG)
            (self.root / "src/main/a.py").write_text("y = eval('1')\n")
            env = {"GATE_RULES": "rules", "GATE_MAIN_PATHS": "src/main", "GATE_AST_GREP": "", "PATH": str(bin_dir)}
            checks: list[tuple[bool, str]] = []
            with mock.patch.dict(os.environ, env):
                t.gate_rules(self.root, lambda ok, what, sink=checks: sink.append((bool(ok), what)), {})
            self.assertTrue(next(ok for ok, w in checks if w.startswith("ast-grep found")), name)
            self.assertFalse(checks[-1][0], name)  # and it really scanned: the planted eval is caught

    def test_matches_are_named_by_rule_id_with_the_file_name_as_fallback(self) -> None:
        (self.root / "rules/eval-call.yml").rename(self.root / "rules/other-name.yml")
        (self.root / "src/main/a.py").write_text("y = eval('1')\n")
        metrics: dict = {}
        with mock.patch.dict(os.environ, {"GATE_RULES": "rules", "GATE_MAIN_PATHS": "src/main", "GATE_AST_GREP": SG}):
            t.gate_rules(self.root, lambda ok, what: None, metrics)
        self.assertEqual(metrics["rule_matches"], ["eval-call: src/main/a.py:1"])  # the id, not "other-name"
        fake = Path(tempfile.mkdtemp()) / "sg"  # an ast-grep whose JSON carries no ruleId
        out = f'[{{"file": "{self.root}/src/main/a.py", "range": {{"start": {{"line": 0}}, "end": {{"line": 0}}}}, "text": "x"}}]'
        fake.write_text(f"#!/bin/sh\nprintf '%s' '{out}'\nexit 1\n")
        fake.chmod(0o755)
        metrics = {}
        with mock.patch.dict(os.environ, {"GATE_RULES": "rules", "GATE_MAIN_PATHS": "src/main", "GATE_AST_GREP": str(fake)}):
            t.gate_rules(self.root, lambda ok, what: None, metrics)
        self.assertEqual(metrics["rule_matches"], ["other-name: src/main/a.py:1"])


if __name__ == "__main__":
    unittest.main()
