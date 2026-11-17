"""Tests for eval-tiering/scripts/list_tests.py on a fixture repo with one test per tier.

Run: python3 skills/regen/tests/test_list_tests.py
"""
import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixtures as fx  # noqa: E402

ET = fx.REGEN / "eval-tiering"
SCRIPT = ET / "scripts" / "list_tests.py"

M = "src/main/kotlin/com/acme/offers/"
T = "src/test/kotlin/com/acme/offers/"

HEAD = "package com.acme.offers\n\nimport io.kotest.core.spec.style.FunSpec\n\n"

FILES = {
    M + "Cooldown.kt": (
        "package com.acme.offers\n\ninternal fun computeCooldownWindow(x: Int) = x\n\n"
        'class Sender(val meter: Meter) { fun go() { meter.counter("offers.sent").increment() } }\n'),
    T + "WireContractTest.kt": HEAD + (
        "class WireContractTest : FunSpec({\n"
        '    test("sends the vendor wire format") {\n'
        "        val server = MockWebServer()\n"
        '        val body = javaClass.getResource("/golden/request.json")\n'
        "    }\n"
        '    test("rejects malformed payloads") { }\n'
        "})\n"),
    T + "ServiceMockTest.kt": (
        "package com.acme.offers\n\nimport io.kotest.core.spec.style.StringSpec\nimport io.mockk.mockk\n\n"
        "class ServiceMockTest : StringSpec({\n"
        '    "calls the repository once" {\n'
        "        val repo = mockk<Repo>()\n"
        "        every { repo.find(any()) } returns null\n"
        '        verify(exactly = 1) { repo.find("a") }\n'
        "    }\n"
        "})\n"),
    T + "MixedTest.kt": HEAD + 'class MixedTest : FunSpec({\n    test("wire call") { MockWebServer(); mockk<X>() }\n})\n',
    T + "PropertyTest.kt": HEAD + 'class PropertyTest : FunSpec({\n    test("window is monotonic") { checkAll(Arb.int()) { } }\n})\n',
    T + "PlainTest.kt": HEAD + 'class PlainTest : FunSpec({\n    test("adds numbers") { }\n})\n',
    T + "InternalTest.kt": HEAD + 'class InternalTest : FunSpec({\n    test("window math") { computeCooldownWindow(3) }\n})\n',
    T + "AppTest.kt": ("package com.acme.offers\n\nimport io.kotest.core.spec.style.FunSpec\n\n"
                       '@MicronautTest\nclass AppTest : FunSpec({ test("boots the app") { } })\n'),
    T + "CommentOnlyTest.kt": HEAD + ('// mocks and contract talk: mockk verify(...) schema pact\n'
                                      'class CommentOnlyTest : FunSpec({ test("only prose mentions") { } })\n'),
    "src/test/kotlin/com/acme/other/OtherTest.kt": (
        "package com.acme.other\nimport io.kotest.core.spec.style.FunSpec\n"
        'class OtherTest : FunSpec({ test("something else") { } })\n'),
    "tests/test_thing.py": "import pytest\n\ndef test_a_thing():\n    pass\n",
    "ops/entity.datadog.yaml": "apiVersion: v3\n",
    "infra/mon.tf": 'resource "datadog_monitor" "m" {}\n',
    "infra/other.tf": 'resource "aws_s3_bucket" "b" {}\n',
    "k6/load.js": "export default function() {}\n",
}


def list_tests(repo, *args):
    p = fx.run([fx.PY, SCRIPT, *args, "--format", "json"], cwd=repo)
    return p, (json.loads(p.stdout) if p.returncode == 0 else None)


class Classification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = fx.init_repo()
        fx.commit(cls.repo, "init", FILES)
        cls.proc, cls.data = list_tests(cls.repo, cls.repo / "src" / "test")
        cls.files = {Path(f["file"]).name: f for f in cls.data["test_files"]}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)

    def tier(self, name):
        return self.files[name]["suggested_tier"]

    def test_ok_and_only_requested_paths(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertEqual(len(self.files), 9)  # the python test lives outside src/test
        self.assertNotIn("test_thing.py", self.files)

    def test_tiers(self):
        self.assertEqual(self.tier("WireContractTest.kt"), "durable")
        self.assertEqual(self.tier("PropertyTest.kt"), "durable")
        self.assertEqual(self.tier("ServiceMockTest.kt"), "ephemeral")
        self.assertEqual(self.tier("InternalTest.kt"), "ephemeral")
        self.assertEqual(self.tier("MixedTest.kt"), "mixed")
        self.assertEqual(self.tier("PlainTest.kt"), "durable-candidate")

    def test_internal_symbol_coupling_detected(self):
        f = self.files["InternalTest.kt"]
        self.assertEqual(f["internal_refs"], 1)
        self.assertIn("1 internal-symbol refs", f["why"])
        self.assertEqual(self.files["PlainTest.kt"]["internal_refs"], 0)

    def test_signals(self):
        s = self.files["ServiceMockTest.kt"]["signals"]
        self.assertGreaterEqual(s["mocks"], 1)
        self.assertGreaterEqual(s["verify"], 1)
        self.assertEqual(self.files["WireContractTest.kt"]["signals"]["http_boundary"], 1)
        self.assertGreaterEqual(self.files["WireContractTest.kt"]["signals"]["golden_files"], 1)
        self.assertEqual(self.files["PropertyTest.kt"]["signals"]["property"], 2)  # checkAll + Arb.

    def test_comments_do_not_count_as_signals(self):
        f = self.files["CommentOnlyTest.kt"]
        self.assertEqual(f["suggested_tier"], "durable-candidate")
        self.assertEqual(sum(f["signals"].values()), 0)

    def test_framework_and_cases(self):
        self.assertEqual(self.files["WireContractTest.kt"]["framework"], "kotest")
        self.assertEqual(self.files["WireContractTest.kt"]["cases"],
                         ["sends the vendor wire format", "rejects malformed payloads"])
        self.assertEqual(self.files["ServiceMockTest.kt"]["cases"], ["calls the repository once"])

    def test_app_context_flag(self):
        self.assertTrue(self.files["AppTest.kt"]["needs_app_or_container"])
        self.assertFalse(self.files["PlainTest.kt"]["needs_app_or_container"])

    def test_language_rewrite_question(self):
        self.assertEqual(self.files["ServiceMockTest.kt"]["language_rewrite"], "invalidated")
        self.assertEqual(self.files["WireContractTest.kt"]["language_rewrite"], "survives if run against the wire")
        self.assertTrue(self.files["MixedTest.kt"]["language_rewrite"].startswith("partly"))

    def test_live_evaluations_and_metrics(self):
        self.assertEqual({(x["kind"], x["file"]) for x in self.data["live"]}, {
            ("monitor/slo (terraform)", "infra/mon.tf"),  # infra/other.tf has no monitor/SLO: excluded
            ("k6", "k6/load.js"),
            ("datadog-entity", "ops/entity.datadog.yaml")})
        self.assertEqual(self.data["metrics_emitted"], ["offers.sent"])

    def test_for_main_path_keeps_only_tests_importing_its_package(self):
        _, data = list_tests(self.repo, self.repo / "src", "--for", self.repo / M.rstrip("/"))
        names = {Path(f["file"]).name for f in data["test_files"]}
        self.assertIn("WireContractTest.kt", names)
        self.assertNotIn("OtherTest.kt", names)
        self.assertNotIn("test_thing.py", names)

    def test_markdown_report(self):
        p = fx.run([fx.PY, SCRIPT, self.repo / "src" / "test"], cwd=self.repo)
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("9 test files, 10 cases", p.stdout)
        self.assertIn("| ServiceMockTest.kt | kotest | 1 | ephemeral | invalidated |", p.stdout)
        self.assertIn("- datadog-entity: ops/entity.datadog.yaml", p.stdout)
        self.assertIn("- offers.sent", p.stdout)

    def test_pytest_files_are_classified_too(self):
        _, data = list_tests(self.repo, self.repo / "tests")
        (f,) = data["test_files"]
        self.assertEqual((f["framework"], f["cases"]), ("pytest", ["test_a_thing"]))


class ShippedExamples(unittest.TestCase):
    def test_example_wire_contract_test_is_durable(self):
        repo = fx.init_repo()
        self.addCleanup(shutil.rmtree, repo, True)
        files = {f"src/test/kotlin/{p.name}": p.read_text() for p in (ET / "examples").glob("*.kt")}
        fx.commit(repo, "examples", files)
        _, data = list_tests(repo, repo / "src" / "test")
        by = {Path(f["file"]).name: f for f in data["test_files"]}
        self.assertEqual(by["VendorWireContractTest.kt"]["suggested_tier"], "durable")
        self.assertEqual(by["VendorWireContractTest.kt"]["framework"], "junit5")


class Errors(unittest.TestCase):
    def test_outside_git_repo(self):
        d = fx.tempdir()
        self.addCleanup(shutil.rmtree, d, True)
        fx.write(d, "ATest.kt", "class ATest\n")
        p = fx.run([fx.PY, SCRIPT, d])
        self.assertEqual(p.returncode, 2)
        self.assertIn("not inside a git repository", p.stderr)


if __name__ == "__main__":
    unittest.main()
