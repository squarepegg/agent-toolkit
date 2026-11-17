"""Tests for _shared/scripts/mutate.py. The "test suite" under mutation is a tiny python script that
writes a JUnit XML file, so no build tool is needed.

Run: python3 skills/regen/tests/test_mutate.py
"""
import contextlib
import io
import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixtures as fx  # noqa: E402

SCRIPT = fx.SHARED / "mutate.py"
sys.path.insert(0, str(fx.SHARED))
import mutate  # noqa: E402

SOURCE = """def allowed(now, last, window):
    return now < last + window


def clamp(x):
    return max(0, x)
"""

# Runs "tests" against source.py and writes results/TEST-x.xml like a build tool would.
RUNNER = '''
import sys, pathlib
from xml.sax.saxutils import quoteattr
ns = {}
exec(pathlib.Path("source.py").read_text(), ns)
cases = []
def case(name, fn):
    try:
        ok = fn()
    except Exception:
        ok = False
    cases.append((name, ok))
case("boundary", lambda: ns["allowed"](10, 0, 10) is False)
case("inside", lambda: ns["allowed"](5, 0, 10) is True)
case("clamp", lambda: ns["clamp"](-3) == 0)
case("vacuous", lambda: True)
out = ['<testsuite name="SuiteSpec">']
for n, ok in cases:
    out.append(f'<testcase classname="com.acme.CooldownSpec" name={quoteattr(n)}>' + ("" if ok else "<failure message='x'/>") + "</testcase>")
out.append("</testsuite>")
pathlib.Path("results").mkdir(exist_ok=True)
pathlib.Path("results/TEST-x.xml").write_text("\\n".join(out))
sys.exit(0 if all(ok for _, ok in cases) else 1)
'''


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = fx.tempdir()
        self.addCleanup(shutil.rmtree, self.dir, True)
        fx.write(self.dir, "source.py", SOURCE)
        fx.write(self.dir, "runner.py", RUNNER)

    def mutants(self, items):
        return fx.write(self.dir, "mutants.json", json.dumps(items))

    def mutate(self, items, *extra, fmt="json"):
        self.mutants(items)
        return fx.run([fx.PY, SCRIPT, "mutants.json", "--results", "results/*.xml", "--format", fmt,
                       *extra, "--", fx.PY, "runner.py"], cwd=self.dir)

    @staticmethod
    def m(name, old, new, file="source.py"):
        return {"name": name, "file": file, "old": old, "new": new}


class Run(Base):
    def test_killed_survived_and_restore(self):
        p = self.mutate([
            self.m("boundary", "now < last + window", "now <= last + window"),
            self.m("clamp-floor", "max(0, x)", "max(1, x)"),
            self.m("equivalent", "def allowed(now, last, window):", "def allowed(now, last, window,):"),
        ])
        self.assertEqual(p.returncode, 0, p.stderr)
        out = {o["mutant"]: o for o in json.loads(p.stdout)["outcomes"]}
        self.assertEqual(out["boundary"]["result"], "killed")
        self.assertEqual(out["boundary"]["killed_by"], ["CooldownSpec › boundary"])
        self.assertEqual(out["clamp-floor"]["result"], "killed")
        self.assertEqual(out["clamp-floor"]["killed_by"], ["CooldownSpec › clamp"])
        self.assertEqual(out["equivalent"]["result"], "survived")
        self.assertEqual(out["equivalent"]["killed_by"], [])
        self.assertEqual((self.dir / "source.py").read_text(), SOURCE)

    def test_syntax_error_mutant_is_no_build_not_killed(self):
        # a SyntaxError makes the runner crash before writing results: no failed test, non-zero exit
        p = self.mutate([self.m("broken", "return now < last + window", "return now <")])
        (o,) = json.loads(p.stdout)["outcomes"]
        self.assertEqual(o["result"], "no-build")
        self.assertNotEqual(o["exit_code"], 0)
        self.assertEqual((self.dir / "source.py").read_text(), SOURCE)

    def test_timeout_is_no_build_and_file_restored(self):
        fx.write(self.dir, "runner.py", "import time\ntime.sleep(30)\n")
        p = self.mutate([self.m("slow", "max(0, x)", "max(1, x)")], "--no-baseline", "--timeout", "1")
        (o,) = json.loads(p.stdout)["outcomes"]
        self.assertEqual((o["result"], o["exit_code"]), ("no-build", None))
        self.assertEqual((self.dir / "source.py").read_text(), SOURCE)

    def test_watch_lists_tests_that_killed_nothing(self):
        p = self.mutate([self.m("boundary", "now < last + window", "now <= last + window")],
                        "--watch", "Cooldown")
        data = json.loads(p.stdout)
        self.assertEqual(data["watched_killed_nothing"],
                         ["CooldownSpec › clamp", "CooldownSpec › inside", "CooldownSpec › vacuous"])

    def test_markdown_output(self):
        p = self.mutate([self.m("boundary", "now < last + window", "now <= last + window"),
                         self.m("equivalent", "def allowed(now, last, window):", "def allowed(now, last, window,):")],
                        "--watch", "vacuous", fmt="md")
        self.assertIn("1 of 2 mutants killed", p.stdout)
        self.assertIn("| boundary | killed | CooldownSpec › boundary |", p.stdout)
        self.assertIn("| equivalent | survived | - |", p.stdout)
        self.assertIn("that killed no mutant: CooldownSpec › vacuous", p.stdout)

    def test_baseline_failure_aborts_without_mutating(self):
        fx.write(self.dir, "source.py", SOURCE.replace("max(0, x)", "max(5, x)"))
        p = self.mutate([self.m("boundary", "now < last + window", "now <= last + window")])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("baseline failed", p.stderr)
        self.assertIn("now < last + window", (self.dir / "source.py").read_text())

    def test_no_baseline_skips_check(self):
        fx.write(self.dir, "source.py", SOURCE.replace("max(0, x)", "max(5, x)"))
        p = self.mutate([self.m("boundary", "now < last + window", "now <= last + window")], "--no-baseline")
        self.assertEqual(p.returncode, 0, p.stderr)
        (o,) = json.loads(p.stdout)["outcomes"]
        self.assertEqual(o["result"], "killed")

    def test_stale_results_are_deleted_before_each_run(self):
        # a leftover failing result file must not make a surviving mutant look killed
        fx.write(self.dir, "results/TEST-stale.xml",
                 '<testsuite name="S"><testcase classname="x.Stale" name="old"><failure/></testcase></testsuite>')
        p = self.mutate([self.m("equivalent", "def allowed(now, last, window):", "def allowed(now, last, window,):")],
                        "--no-baseline")
        (o,) = json.loads(p.stdout)["outcomes"]
        self.assertEqual(o["result"], "survived")


class Rejection(Base):
    def assert_rejected(self, items, needle):
        p = self.mutate(items)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("mutants rejected, nothing changed", p.stderr)
        self.assertIn(needle, p.stderr)
        self.assertEqual((self.dir / "source.py").read_text(), SOURCE)
        self.assertFalse((self.dir / "results").exists(), "no test run should have happened")

    def test_old_must_occur_exactly_once(self):
        self.assert_rejected([self.m("dup", "now", "later")], "occurs 2 times")
        self.assert_rejected([self.m("none", "does not exist", "x")], "occurs 0 times")

    def test_identical_old_new(self):
        self.assert_rejected([self.m("same", "max(0, x)", "max(0, x)")], "identical")

    def test_missing_file(self):
        self.assert_rejected([self.m("nofile", "a", "b", file="missing.py")], "file not found")

    def test_one_bad_mutant_rejects_the_whole_batch(self):
        self.assert_rejected([self.m("good", "max(0, x)", "max(1, x)"), self.m("bad", "zzz", "y")], "bad: 'old' occurs 0")

    def test_malformed_json_and_missing_keys(self):
        fx.write(self.dir, "mutants.json", "{not json")
        p = fx.run([fx.PY, SCRIPT, "mutants.json", "--results", "r/*.xml", "--", "true"], cwd=self.dir)
        self.assertIn("cannot read mutants", p.stderr)
        fx.write(self.dir, "mutants.json", json.dumps([{"name": "x"}]))
        p = fx.run([fx.PY, SCRIPT, "mutants.json", "--results", "r/*.xml", "--", "true"], cwd=self.dir)
        self.assertIn("cannot read mutants", p.stderr)

    def test_missing_command(self):
        p = fx.run([fx.PY, SCRIPT, "mutants.json", "--results", "r/*.xml"], cwd=self.dir)
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("usage", p.stderr)


class JunitParsing(Base):
    def parse(self, xml):
        fx.write(self.dir, "results/a.xml", xml)
        glob = [str(self.dir / "results" / "*.xml")]
        with contextlib.redirect_stderr(io.StringIO()):
            return mutate.failed_tests(glob), mutate.all_tests(glob)

    def test_failure_and_error_both_count(self):
        failed, every = self.parse(
            '<testsuites><testsuite name="S"><testcase classname="a.b.C" name="one"><failure/></testcase>'
            '<testcase classname="a.b.C" name="two"><error/></testcase>'
            '<testcase classname="a.b.C" name="three"/></testsuite></testsuites>')
        self.assertEqual(failed, ["C › one", "C › two"])
        self.assertEqual(every, ["C › one", "C › three", "C › two"])

    def test_suite_name_used_without_classname(self):
        failed, _ = self.parse('<testsuite name="x.Y"><testcase name="t"><failure/></testcase></testsuite>')
        self.assertEqual(failed, ["Y › t"])

    def test_dtd_is_refused(self):
        failed, every = self.parse(
            '<!DOCTYPE x [<!ENTITY e "boom">]><testsuite name="S"><testcase name="t"><failure/></testcase></testsuite>')
        self.assertEqual((failed, every), ([], []))

    def test_unparseable_file_is_skipped(self):
        failed, every = self.parse("<testsuite><unclosed>")
        self.assertEqual((failed, every), ([], []))


if __name__ == "__main__":
    unittest.main()
