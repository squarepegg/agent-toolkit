#!/usr/bin/env python3
"""Tests for guard.py and templates/table_runner.py (stdlib, no model). python3 skills/tests-as-data/tests/test_tests_as_data.py
The guard cases are a table: (task, changed file) -> allowed?"""
import importlib.util, os, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); SK = os.path.dirname(HERE)


def load(rel):
    spec = importlib.util.spec_from_file_location(os.path.basename(rel)[:-3], os.path.join(SK, rel))
    m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m); return m


guard, runner = load("scripts/guard.py"), load("templates/table_runner.py")
CFG = {"data": ["tests/data/**"], "golden": ["**/*.golden"], "runner": ["tests/runner/**", "tests/*.py"]}
# task, file, allowed
GUARD = [
    ("feature", "src/a.kt", True), ("feature", "tests/data/x.csv", True), ("feature", "t/x.golden", True),
    ("feature", "tests/runner/run.py", False), ("feature", "tests/conftest.py", False),
    ("refactor", "src/a.kt", True), ("refactor", "tests/data/x.csv", False), ("refactor", "t/x.golden", False),
    ("refactor", "tests/runner/run.py", False),
    ("coverage", "src/a.kt", False), ("coverage", "tests/data/x.csv", True), ("coverage", "tests/runner/run.py", False),
    ("dsl", "tests/runner/run.py", True), ("dsl", "src/a.kt", False), ("dsl", "t/x.golden", True),
]


class Guard(unittest.TestCase):
    def test_matrix(self):
        for task, f, ok in GUARD:
            self.assertEqual(not guard.violations(task, [f], CFG), ok, (task, f))

    def test_runner_wins_over_data_when_both_match(self):
        cfg = {"data": ["tests/**"], "runner": ["tests/runner/**"]}
        self.assertEqual(guard.classify("tests/runner/a.py", cfg), "runner")

    def test_globs(self):
        self.assertTrue(guard.glob_re("a/**/b.py").match("a/b.py"))
        self.assertTrue(guard.glob_re("a/**/b.py").match("a/x/y/b.py"))
        self.assertFalse(guard.glob_re("a/*.py").match("a/x/b.py"))
        self.assertFalse(guard.glob_re("a/*.py").match("a/xbpy"))

    def test_cli_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            c = os.path.join(d, "c.json")
            with open(c, "w") as f: f.write('{"data": ["d/**"]}')
            def go(task, *files):
                return subprocess.run([sys.executable, os.path.join(SK, "scripts/guard.py"), "--task", task, "--config", c, "--files", *files],
                                      capture_output=True, text=True)
            self.assertEqual(go("refactor", "src/a").returncode, 0)
            r = go("refactor", "src/a", "d/x.csv")
            self.assertEqual(r.returncode, 1); self.assertIn("d/x.csv", r.stdout)
            self.assertEqual(go("nonsense", "a").returncode, 2)

    def test_git_base(self):
        with tempfile.TemporaryDirectory() as d:
            g = lambda *a: subprocess.run(["git", "-C", d, "-c", "user.email=t@t", "-c", "user.name=t", *a], check=True, capture_output=True)
            def w(rel, s):
                with open(os.path.join(d, rel), "w") as f: f.write(s)
            g("init", "-q"); os.makedirs(os.path.join(d, "d")); w("d/x.csv", "a\n")
            w("c.json", '{"data": ["d/**"]}'); g("add", "."); g("commit", "-qm", "1")
            w("d/x.csv", "b\n")
            r = subprocess.run([sys.executable, os.path.join(SK, "scripts/guard.py"), "--task", "refactor", "--config", os.path.join(d, "c.json"), "--base", "HEAD"],
                               capture_output=True, text=True, cwd=d)
            self.assertEqual(r.returncode, 1, r.stdout + r.stderr)


def upper(text=None, s=None):
    return (text if text is not None else s).upper()


class Runner(unittest.TestCase):
    def mk(self, files):
        d = tempfile.mkdtemp()
        for n, t in files.items():
            with open(os.path.join(d, n), "w") as f: f.write(t)
        return d

    def test_table_pass_and_mismatch_diff(self):
        d = self.mk({"a.csv": "s,expected\nab,AB\ncd,XX\n"})
        out = runner.run(d, target=upper)
        self.assertEqual(len(out), 1); self.assertIn("a.csv:3", out[0]); self.assertIn("-XX", out[0]); self.assertIn("+CD", out[0])

    def test_blank_expected_is_a_parse_error(self):
        d = self.mk({"a.csv": "s,expected\nab,\n"})
        with self.assertRaises(ValueError): runner.run(d, target=upper)

    def test_nondeterministic_target_is_reported(self):
        n = iter(range(100))
        d = self.mk({"a.csv": "s,expected\nab,AB\n"})
        out = runner.run(d, target=lambda s: str(next(n)))
        self.assertIn("NON-DETERMINISTIC", out[0])

    def test_golden_missing_then_approved_then_stable(self):
        d = self.mk({"x.in": "hi"})
        self.assertTrue(runner.run(d, target=upper))                    # no golden yet: fails
        os.environ.pop("CI", None)
        self.assertEqual(runner.run(d, target=upper, approve=True), [])  # human approves
        with open(os.path.join(d, "x.golden")) as f: self.assertEqual(f.read(), "HI")
        self.assertEqual(runner.run(d, target=upper), [])
        self.assertTrue(runner.run(d, target=lambda t: t))              # behaviour changed: fails with a diff

    def test_approve_refused_in_ci(self):
        d = self.mk({"x.in": "hi"})
        os.environ["CI"] = "1"
        try:
            with self.assertRaises(SystemExit): runner.run(d, target=upper, approve=True)
        finally: os.environ.pop("CI")
        self.assertFalse(os.path.exists(os.path.join(d, "x.golden")))

    def test_approve_does_not_bless_nondeterministic_output(self):
        n = iter(range(100)); d = self.mk({"x.in": "hi"}); os.environ.pop("CI", None)
        out = runner.run(d, target=lambda t: str(next(n)), approve=True)
        self.assertIn("NON-DETERMINISTIC", out[0]); self.assertFalse(os.path.exists(os.path.join(d, "x.golden")))


if __name__ == "__main__":
    unittest.main()
