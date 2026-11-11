#!/usr/bin/env python3
"""End to end through the real CLI: `sdlc_tool.py gate-step --name static` with GATE_RULES set records a failing step
(and names the rule and line) when main sources match an adopted rule, a passing one when they do not, and nothing
rule-related when GATE_RULES is unset. This is the path gate.sh takes."""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOL = ROOT / "scripts/sdlc_tool.py"
_v = importlib.util.spec_from_file_location("rule_validate", ROOT / "scripts/rule_validate.py")
if _v is None or _v.loader is None:
    raise RuntimeError("cannot load rule_validate.py")
rv = importlib.util.module_from_spec(_v)
_v.loader.exec_module(rv)

RULE = "id: eval-call\nlanguage: python\nseverity: error\nmessage: no eval\nrule:\n  pattern: eval($X)\n"


class GateStep(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        (self.root / "src/main").mkdir(parents=True)
        (self.root / "rules").mkdir()
        (self.root / "rules/eval-call.yml").write_text(RULE)
        (self.root / "src/main/a.py").write_text("x = int('1')\n")
        self.out = Path(tempfile.mkdtemp())

    def static_step(self, **env: str) -> dict:
        e = {k: v for k, v in os.environ.items() if not k.startswith("GATE_")}
        e.update({"GATE_STATIC_LANGS": "py", "GATE_MAIN_PATHS": "src/main", "GATE_AST_GREP": rv.find_sg() or "", **env})
        r = subprocess.run([sys.executable, str(TOOL), "gate-step", "--dir", str(self.out), "--root", str(self.root),
                            "--name", "static", "--mode", "required", "--order", "5", "--cmd", "true", "--exit", "0",
                            "--log", "x.log", "--seconds", "1", "--started", "0"], capture_output=True, text=True, env=e)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads((self.out / "steps/05-static.json").read_text())

    def test_clean_sources_pass_and_the_rule_check_is_recorded(self) -> None:
        step = self.static_step(GATE_RULES="rules")
        self.assertEqual(step["status"], "pass", step)
        self.assertTrue(any("project rule(s) match nothing" in c["what"] for c in step["checks"]), step["checks"])

    def test_a_planted_match_fails_the_step_and_names_rule_and_line(self) -> None:
        (self.root / "src/main/a.py").write_text("x = 1\ny = eval('1')\n")
        step = self.static_step(GATE_RULES="rules")
        self.assertEqual(step["status"], "fail")
        self.assertEqual(step["metrics"]["rule_matches"], ["eval-call: src/main/a.py:2"])
        self.assertTrue(any(not c["ok"] and "match nothing" in c["what"] for c in step["checks"]))

    def test_without_gate_rules_no_rule_check_runs(self) -> None:
        (self.root / "src/main/a.py").write_text("y = eval('1')\n")
        step = self.static_step()
        self.assertEqual(step["status"], "pass")
        self.assertFalse(any("rule" in c["what"] for c in step["checks"]), step["checks"])

    def test_bad_rule_setups_fail_the_step(self) -> None:
        for env in ({"GATE_RULES": "empty"}, {"GATE_RULES": ".sdlc/rules"}, {"GATE_RULES": "rules", "GATE_AST_GREP": "/nonexistent/sg"}):
            (self.root / "empty").mkdir(exist_ok=True)
            step = self.static_step(**env)
            self.assertEqual(step["status"], "fail", env)

    def test_a_missing_ast_grep_binary_is_a_failed_step_not_a_pass(self) -> None:
        step = self.static_step(GATE_RULES="rules", GATE_AST_GREP="/nonexistent/sg", PATH="/usr/bin:/bin")
        self.assertEqual(step["status"], "fail")
        self.assertTrue(any(not c["ok"] and c["what"].startswith("ast-grep found") for c in step["checks"]), step["checks"])
        self.assertFalse(any("unreadable" in c["what"] for c in step["checks"]))  # a clean failure, not a crash


if __name__ == "__main__":
    unittest.main()
