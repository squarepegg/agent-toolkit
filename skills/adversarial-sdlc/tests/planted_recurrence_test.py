#!/usr/bin/env python3
"""AC-14: a planted recurrence of each consolidated rule's class fails the gate's static step (GATE_RULES), and the
rule's valid (fixed) form passes it. The planted code is each rule's own test case (tests/rule-tests), written into a
project's main sources; the rule is adopted as a project rule, as a relock would do."""
from __future__ import annotations

import importlib.util
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock


def read_yaml(path: Path):
    """The rule cases are quoted YAML scalars; a missing parser must fail the test, not skip it."""
    try:
        import yaml
    except ImportError as e:
        raise AssertionError("PyYAML is needed to read tests/rule-tests (pip install pyyaml)") from e
    return yaml.safe_load(path.read_text())


ROOT = Path(__file__).resolve().parent.parent


def load(name: str, rel: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    if spec is None or spec.loader is None:
        raise RuntimeError(rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


t = load("sdlc_tool", "scripts/sdlc_tool.py")
rv = load("rule_validate", "scripts/rule_validate.py")
RULES = sorted((ROOT / "tests/rules").glob("*.yml"))


def language(rule: Path) -> str:
    return rv.rule_language(rule)


class Planted(unittest.TestCase):
    def test_a_yaml_parser_and_ast_grep_exist(self) -> None:
        read_yaml(next((ROOT / "tests/rule-tests").glob("*-test.yml")))
        self.assertTrue(rv.find_sg(), "ast-grep not found (npm i -g @ast-grep/cli)")
        self.assertGreaterEqual(len(RULES), 12)

    def run_gate(self, rule: Path, code: str) -> tuple[list[tuple[bool, str]], dict]:
        root = Path(tempfile.mkdtemp())
        (root / "src/main").mkdir(parents=True)
        (root / "rules").mkdir()
        shutil.copy(rule, root / "rules" / rule.name)
        (root / "src/main" / f"planted{rv.LANG_EXT[language(rule)]}").write_text(code + "\n")
        out: list[tuple[bool, str]] = []
        metrics: dict = {}
        env = {"GATE_RULES": "rules", "GATE_MAIN_PATHS": "src/main", "GATE_AST_GREP": rv.find_sg() or ""}
        with mock.patch.dict(os.environ, env):
            t.gate_rules(root, lambda ok, what: out.append((bool(ok), what)), metrics)
        return out, metrics

    def test_every_rule_fails_on_its_planted_case_and_passes_on_its_fix(self) -> None:
        for rule in RULES:
            # the test file is named by rule id, which can differ from the rule's file name
            rid = next(ln.split(":", 1)[1].strip() for ln in rule.read_text().splitlines() if ln.startswith("id:"))
            case = read_yaml(ROOT / "tests/rule-tests" / f"{rid}-test.yml")
            with self.subTest(rule=rid):
                for bad in case["invalid"]:
                    res, metrics = self.run_gate(rule, bad)
                    self.assertFalse(all(ok for ok, _ in res), f"planted case not caught: {bad!r}")
                    self.assertTrue(any(m.startswith(rid) for m in metrics["rule_matches"]), metrics)
                for good in case["valid"]:
                    res, _ = self.run_gate(rule, good)
                    self.assertTrue(all(ok for ok, _ in res), f"fixed form flagged: {good!r} {res}")


if __name__ == "__main__":
    unittest.main()
