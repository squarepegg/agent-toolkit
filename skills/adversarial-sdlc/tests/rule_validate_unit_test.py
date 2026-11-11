#!/usr/bin/env python3
"""B2 validation (rule_validate.py): a proposed rule is evidence only if it fires inside the fix's changed lines in the
pre-fix snapshot, is silent on the fixed one, passes its standalone test, and leaves no unlisted match in the current
snapshot. A missing ast-grep fails (a check that does not run must not read as a pass)."""
from __future__ import annotations

import importlib.util
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("rule_validate", ROOT / "scripts/rule_validate.py")
assert _spec is not None and _spec.loader is not None
rv = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(rv)

RULE = """id: eval-call
language: python
severity: error
message: do not call eval
rule:
  pattern: eval($X)
"""
PRE = "def f(s):\n    x = 1\n    return eval(s)\n"
FIXED = "def f(s):\n    x = 1\n    return int(s)\n"


class Validate(unittest.TestCase):
    def setUp(self) -> None:
        self.sg = rv.find_sg()
        self.assertIsNotNone(self.sg, "ast-grep not found (npm i -g @ast-grep/cli)")
        d = Path(tempfile.mkdtemp())
        self.rule = d / "rule.yml"
        self.rule.write_text(RULE)
        self.pre, self.fixed, self.cur = d / "pre", d / "fixed", d / "cur"
        for p, body in ((self.pre, PRE), (self.fixed, FIXED), (self.cur, FIXED)):
            p.mkdir()
            (p / "m.py").write_text(body)

    def run_v(self, **kw):
        return rv.validate(self.rule, self.pre, self.fixed, kw.get("cur", self.cur), sg=self.sg)

    def test_accepts_a_rule_that_fires_on_the_fixed_line_only(self) -> None:
        r = self.run_v()
        self.assertTrue(r["accepted"], r)
        self.assertEqual(r["pre_hits_in_hunks"], ["m.py:3"])

    def test_vacuous_rule_is_refused(self) -> None:
        self.rule.write_text(RULE.replace("eval($X)", "exec($X)"))
        r = self.run_v()
        self.assertFalse(r["accepted"])
        self.assertIn("vacuous", r["reason"])

    def test_match_outside_the_changed_lines_is_vacuous(self) -> None:
        # the fix changed line 3, the rule only matches the untouched line 2
        self.rule.write_text(RULE.replace("eval($X)", "$A = 1"))
        r = self.run_v()
        self.assertFalse(r["accepted"])
        self.assertIn("vacuous", r["reason"])
        self.assertEqual(r["pre_hits_outside_hunks"], ["m.py:2"])

    def test_rule_matching_the_fixed_snapshot_is_refused(self) -> None:
        (self.fixed / "m.py").write_text("def f(s):\n    x = 1\n    return eval(str(s))\n")  # changed, still eval
        r = self.run_v()
        self.assertFalse(r["accepted"])
        self.assertIn("fixed snapshot", r["reason"])

    def test_unlisted_current_match_is_refused_and_ignore_comment_accepts(self) -> None:
        (self.cur / "m.py").write_text(FIXED + "eval('1')\n")
        r = self.run_v()
        self.assertFalse(r["accepted"])
        self.assertIn("unlisted", r["reason"])
        self.assertTrue(r["current_matches"][0].startswith("m.py:4"))
        (self.cur / "m.py").write_text(FIXED + "# reason: constant input, reviewed\n# ast-grep-ignore: eval-call\neval('1')\n")
        self.assertTrue(self.run_v()["accepted"])

    def test_deleted_file_counts_as_changed(self) -> None:
        (self.fixed / "m.py").unlink()
        (self.fixed / "other.py").write_text("")
        self.assertTrue(self.run_v()["accepted"])

    def test_budget(self) -> None:
        r = rv.validate(self.rule, self.pre, self.fixed, self.cur, budget=-1, sg=self.sg)
        self.assertFalse(r["accepted"])
        self.assertIn("budget", r["reason"])

    def test_cli_exit_codes(self) -> None:
        base = ["--rule", str(self.rule), "--pre", str(self.pre), "--fixed", str(self.fixed), "--sg", self.sg or ""]
        self.assertEqual(rv.main(base), 0)
        self.assertEqual(rv.main(["--rule", str(self.rule) + "x", "--pre", str(self.pre), "--fixed", str(self.fixed)]), 2)
        self.rule.write_text(RULE.replace("eval($X)", "exec($X)"))
        self.assertEqual(rv.main(base), 1)

    def test_missing_ast_grep_is_an_error(self) -> None:
        with self.assertRaises(RuntimeError):
            rv.validate(self.rule, self.pre, self.fixed, sg="/nonexistent/ast-grep")


CTX_RULE = """id: eval-in-func
language: python
severity: error
message: no eval in a function
rule:
  pattern: eval($X)
  inside:
    kind: function_definition
    stopBy: end
"""


def fake_sg(d: Path, code: int, stdout: str = "", stderr: str = "") -> str:
    """A stand-in ast-grep with a fixed exit code and output, so scan()'s error handling is tested exactly."""
    f = d / f"sg{code}{'e' if stderr else ''}"
    f.write_text(f"#!/bin/sh\nprintf '%s' '{stdout}'\nprintf '%s' '{stderr}' >&2\nexit {code}\n")
    f.chmod(f.stat().st_mode | stat.S_IXUSR)
    return str(f)


class Pieces(unittest.TestCase):
    def setUp(self) -> None:
        self.d = Path(tempfile.mkdtemp())
        self.sg = rv.find_sg() or ""

    # ---- find_sg
    def test_find_sg_prefers_explicit_then_ast_grep_then_sg_then_the_pi_install(self) -> None:
        self.assertEqual(rv.find_sg("/x/y"), "/x/y")
        with mock.patch.object(rv.shutil, "which", side_effect=lambda n: {"ast-grep": "/a/ast-grep"}.get(n)):
            self.assertEqual(rv.find_sg(), "/a/ast-grep")
        with mock.patch.object(rv.shutil, "which", side_effect=lambda n: {"sg": "/a/sg"}.get(n)):
            self.assertEqual(rv.find_sg(), "/a/sg")
        home = self.d / "home"
        (home / ".pi/agent/npm/node_modules/@ast-grep/cli-z").mkdir(parents=True)
        (home / ".pi/agent/npm/node_modules/@ast-grep/cli-z/ast-grep").write_text("")
        with mock.patch.object(rv.shutil, "which", return_value=None), mock.patch.object(rv.Path, "home", return_value=home):
            self.assertTrue(str(rv.find_sg()).endswith("cli-z/ast-grep"))
        with mock.patch.object(rv.shutil, "which", return_value=None), \
                mock.patch.object(rv.Path, "home", return_value=self.d / "empty"):
            self.assertIsNone(rv.find_sg())

    # ---- scan
    def test_scan_exit_codes_and_output_are_handled_exactly(self) -> None:
        rule, root = self.d / "r.yml", self.d / "root"
        rule.write_text(RULE)
        root.mkdir()
        one = f'[{{"file": "{root}/a.py", "range": {{"start": {{"line": 0}}, "end": {{"line": 1}}}}, "text": "eval(x)"}}]'
        self.assertEqual(rv.scan(fake_sg(self.d, 0, "[]"), rule, root), [])
        self.assertEqual(rv.scan(fake_sg(self.d, 1, one), rule, root)[0]["end"], 2)  # exit 1 = matches found
        self.assertEqual(rv.scan(fake_sg(self.d, 0, ""), rule, root), [])  # empty stdout, empty stderr: no matches
        with self.assertRaises(RuntimeError):
            rv.scan(fake_sg(self.d, 2, "[]"), rule, root)  # any other exit code is a failure
        with self.assertRaises(RuntimeError):
            rv.scan(fake_sg(self.d, 0, "", "boom"), rule, root)  # nothing on stdout but a complaint
        with self.assertRaises(RuntimeError):
            rv.scan(fake_sg(self.d, 0, "{not json"), rule, root)
        self.assertEqual(rv.scan("/nonexistent", rule, self.d / "nope"), [])  # absent root: nothing to scan

    def test_scan_is_sorted_and_relative(self) -> None:
        root = self.d / "root"
        (root / "b").mkdir(parents=True)
        (root / "b/z.py").write_text("eval(1)\neval(2)\n")
        (root / "a.py").write_text("eval(3)\n")
        rule = self.d / "r.yml"
        rule.write_text(RULE)
        hits = rv.scan(self.sg, rule, root)
        self.assertEqual([(h["file"], h["line"]) for h in hits], [("a.py", 1), ("b/z.py", 1), ("b/z.py", 2)])
        self.assertEqual(hits, rv.scan(self.sg, rule, root))  # same input, same output

    # ---- changed_old_lines
    def lines(self, old: str, new: str | None):
        a, b = self.d / "o", self.d / "n"
        a.mkdir(exist_ok=True)
        b.mkdir(exist_ok=True)
        (a / "f").write_text(old)
        if new is not None:
            (b / "f").write_text(new)
        elif (b / "f").exists():
            (b / "f").unlink()
        return rv.changed_old_lines(a, b, "f")

    def test_changed_old_lines_exactly(self) -> None:
        self.assertEqual(self.lines("a\nb\nc\n", "a\nX\nc\n"), {2})  # replace
        self.assertEqual(self.lines("a\nb\nc\n", "a\nc\n"), {2})  # delete
        self.assertEqual(self.lines("a\nb\nc\n", "a\nb\nNEW\nc\n"), {2, 3})  # pure insertion: the neighbours
        self.assertEqual(self.lines("a\nb\n", "NEW\na\nb\n"), {0, 1})  # insertion at the top
        self.assertEqual(self.lines("a\nb\n", "a\nb\n"), set())  # unchanged
        self.assertIsNone(self.lines("a\n", None))  # file gone: all of it

    # ---- enclosing_python
    def test_enclosing_python_picks_the_innermost_function_and_its_bounds(self) -> None:
        f = self.d / "m.py"
        f.write_text("x = 1\ndef outer():\n    a = 1\n    def inner():\n        return eval(a)\n    return inner\n")
        self.assertIsNone(rv.enclosing_python(f, 1))  # module level
        self.assertTrue((rv.enclosing_python(f, 5) or "").startswith("def inner():"))
        self.assertTrue((rv.enclosing_python(f, 3) or "").startswith("def outer():"))
        self.assertTrue((rv.enclosing_python(f, 2) or "").startswith("def outer():"))  # first line of the def
        self.assertTrue((rv.enclosing_python(f, 6) or "").startswith("def outer():"))  # last line
        self.assertIsNone(rv.enclosing_python(f, 7))  # past the end
        text = rv.enclosing_python(f, 5) or ""
        self.assertEqual(text.splitlines()[-1], "    return eval(a)")  # dedented, ends at the def's last line
        f.write_text("def broken(:\n")
        self.assertIsNone(rv.enclosing_python(f, 1))

    def test_context_rules_are_tested_on_the_enclosing_function(self) -> None:
        d = self.d
        rule, pre, fixed = d / "ctx.yml", d / "pre", d / "fixed"
        rule.write_text(CTX_RULE)
        pre.mkdir()
        fixed.mkdir()
        (pre / "m.py").write_text("def f(s):\n    x = 1\n    return eval(s)\n")
        (fixed / "m.py").write_text("def f(s):\n    x = 1\n    return int(s)\n")
        r = rv.validate(rule, pre, fixed, fixed, sg=self.sg)
        self.assertTrue(r["accepted"], r)
        self.assertEqual(r["standalone_test"], "rule test passes")

    def test_rule_test_reports_unsupported_languages_instead_of_hiding_it(self) -> None:
        rule = self.d / "x.yml"
        rule.write_text(RULE.replace("language: python", "language: haskell"))
        ok, why = rv.rule_test(self.sg, rule, ["eval x"])
        self.assertTrue(ok)
        self.assertIn("haskell", why)
        self.assertIn("skipped", why)
        rule.write_text("id: nolang\nrule:\n  pattern: x\n")
        self.assertIn("language ?", rv.rule_test(self.sg, rule, ["x"])[1])

    def test_rule_test_names_the_failing_cases(self) -> None:
        rule = self.d / "r.yml"
        rule.write_text(RULE)
        ok, why = rv.rule_test(self.sg, rule, ["eval(x)", "int(x)"])
        self.assertFalse(ok)
        self.assertIn("case1", why)
        self.assertNotIn("case0", why)

    # ---- budget boundary, determinism, CLI
    def test_budget_boundary_is_exclusive_and_result_is_recorded(self) -> None:
        rule, pre, fixed = self.d / "r.yml", self.d / "pre", self.d / "fixed"
        rule.write_text(RULE)
        pre.mkdir()
        fixed.mkdir()
        (pre / "m.py").write_text("eval(1)\n")
        (fixed / "m.py").write_text("int(1)\n")
        for took, budget, expect in ((5.0, 5.0, True), (5.01, 5.0, False)):
            with mock.patch.object(rv.time, "monotonic", side_effect=[100.0, 100.0 + took]):
                r = rv.validate(rule, pre, fixed, fixed, budget=budget, sg=self.sg)
            self.assertEqual(r["accepted"], expect, (took, r))
            self.assertEqual(r["seconds"], took)

    def test_validate_is_deterministic(self) -> None:
        rule, pre, fixed = self.d / "r.yml", self.d / "pre", self.d / "fixed"
        rule.write_text(RULE)
        pre.mkdir()
        fixed.mkdir()
        for n in ("a", "b", "c"):
            (pre / f"{n}.py").write_text("eval(1)\neval(2)\n")
            (fixed / f"{n}.py").write_text("int(1)\nint(2)\n")
        first = rv.validate(rule, pre, fixed, fixed, sg=self.sg)
        first["seconds"] = None
        for _ in range(3):
            again = rv.validate(rule, pre, fixed, fixed, sg=self.sg)
            again["seconds"] = None
            self.assertEqual(first, again)
        self.assertEqual(first["pre_hits_in_hunks"][:2], ["a.py:1", "a.py:2"])

    def test_cli_error_paths_return_exactly_2(self) -> None:
        rule, d = self.d / "r.yml", self.d / "dd"
        rule.write_text(RULE)
        d.mkdir()
        args = ["--rule", str(rule), "--pre", str(d), "--fixed", str(d)]
        self.assertEqual(rv.main([*args, "--sg", "/nonexistent/sg"]), 2)  # tool error
        self.assertEqual(rv.main([*args, "--sg", self.sg]), 1)  # refused (vacuous)
        for missing in ("--rule", "--pre", "--fixed"):
            argv = [x for i, x in enumerate(args) if not (x == missing or args[i - 1] == missing)]
            with self.assertRaises(SystemExit) as cm:
                rv.main(argv)
            self.assertEqual(cm.exception.code, 2)

    def test_remaining_edges(self) -> None:
        # no language line: ""
        rule = self.d / "nolang.yml"
        rule.write_text("id: x\nrule:\n  pattern: y\n")
        self.assertEqual(rv.rule_language(rule), "")
        # a rule that matches only because of file context fails its standalone test and is refused with that reason
        ctx = self.d / "follows.yml"
        ctx.write_text("id: after-import\nlanguage: python\nseverity: error\nmessage: m\nrule:\n  pattern: eval($X)\n"
                       "  inside:\n    kind: module\n    stopBy: end\n    has:\n      kind: import_statement\n")
        pre, fixed = self.d / "p2", self.d / "f2"
        pre.mkdir()
        fixed.mkdir()
        (pre / "m.py").write_text("import os\neval(1)\n")
        (fixed / "m.py").write_text("import os\nint(1)\n")
        r = rv.validate(ctx, pre, fixed, fixed, sg=self.sg)
        self.assertFalse(r["accepted"])
        self.assertIn("does not match its own pre-fix snippet", r["reason"])
        self.assertEqual(r["standalone_test"], r["reason"])
        # seconds are rounded to 2 places
        rule2, pre3, fixed3 = self.d / "r3.yml", self.d / "p3", self.d / "f3"
        rule2.write_text(RULE)
        pre3.mkdir()
        fixed3.mkdir()
        (pre3 / "m.py").write_text("eval(1)\n")
        (fixed3 / "m.py").write_text("int(1)\n")
        with mock.patch.object(rv.time, "monotonic", side_effect=[0.0, 1.2345]):
            self.assertEqual(rv.validate(rule2, pre3, fixed3, fixed3, sg=self.sg)["seconds"], 1.23)
        # current matches are listed with the first line of the match, cut at 100 characters
        (fixed3 / "m.py").write_text("eval(" + "9" * 200 + ")\n")
        cur = rv.validate(rule2, pre3, fixed3, fixed3, sg=self.sg)
        # fixed still matches, so it is refused earlier: use a clean fixed and a dirty current instead
        cur_dir = self.d / "c3"
        cur_dir.mkdir()
        (fixed3 / "m.py").write_text("int(1)\n")
        (cur_dir / "m.py").write_text("eval(" + "9" * 200 + ")\n")
        r2 = rv.validate(rule2, pre3, fixed3, cur_dir, sg=self.sg)
        self.assertEqual(len(r2["current_matches"][0]), len("m.py:1: ") + 100)
        self.assertIn("still matches", cur["reason"])
        # ast-grep's complaint is cut at 300 characters
        rule3, root = self.d / "r4.yml", self.d / "root4"
        rule3.write_text(RULE)
        root.mkdir()
        with self.assertRaises(RuntimeError) as cm:
            rv.scan(fake_sg(self.d, 2, "[]", "e" * 400), rule3, root)
        self.assertEqual(str(cm.exception), "ast-grep scan failed (2): " + "e" * 300)
        # a long file with very repetitive lines is diffed exactly (no autojunk heuristics)
        a, b = self.d / "ja", self.d / "jb"
        a.mkdir()
        b.mkdir()
        (a / "f").write_text("\n".join(["x"] * 300 + ["eval(1)"] + ["x"] * 300) + "\n")
        (b / "f").write_text("\n".join(["x"] * 300 + ["int(1)"] + ["x"] * 300) + "\n")
        self.assertEqual(rv.changed_old_lines(a, b, "f"), {301})


if __name__ == "__main__":
    unittest.main()
