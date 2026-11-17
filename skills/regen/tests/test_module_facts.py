"""Tests for _shared/scripts/module_facts.py on a small Kotlin module fixture in a tempdir git repo.

Run: python3 skills/regen/tests/test_module_facts.py
"""
import json
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixtures as fx  # noqa: E402

sys.path.insert(0, str(fx.SHARED))
import module_facts  # noqa: E402

PKG_DIR = "src/main/kotlin/com/acme/offers"

FILES = {
    "build.gradle.kts": (
        "dependencies {\n"
        '    implementation("io.micronaut:micronaut-http-client:4.0")\n'
        '    testImplementation("io.kotest:kotest-runner:5")\n'
        '    api("com.acme:shared:1.0")\n'
        "}\n"),
    f"{PKG_DIR}/OfferRepository.kt": """package com.acme.offers

import com.acme.shared.Clock
import io.micronaut.http.HttpRequest

// a comment line that does not count toward LOC
interface OfferRepository {
    fun find(id: String): String?
}

class SqlOfferRepository : OfferRepository {
    override fun find(id: String): String? = null

    fun save(id: String) {
        db.execute("INSERT INTO offers (id) VALUES (?)", id)
    }
}
""",
    f"{PKG_DIR}/OfferService.kt": """package com.acme.offers

const val READ_TIMEOUT_MS = 30000

class OfferService(private val repo: OfferRepository) {
    fun grant(id: String) {
        val v = HttpRequest.POST("/api/v2/offers", id)
        repo.find(id)
    }
}

fun helperFunction(x: Int) = x
""",
    "src/main/kotlin/com/acme/other/Billing.kt": """package com.acme.other

import com.acme.offers.OfferService

class Billing(val s: OfferService, val repo: Any) {
    fun run() {
        db.execute("UPDATE offers SET x = 1")
        repo.save("1")
    }
}
""",
    "src/test/kotlin/com/acme/offers/OfferServiceTest.kt": """package com.acme.offers

import io.mockk.mockk

@MicronautTest
class OfferServiceTest {
    val repo = mockk<OfferRepository>()
    val impl = object : OfferRepository { override fun find(id: String) = null }
}
""",
    "src/test/kotlin/com/acme/unrelated/OtherTest.kt": """package com.acme.unrelated

class OtherTest { val x = "OfferRepository" }
""",
    "api/openapi.yaml": "openapi: 3.0.0\n",
}


def facts(repo, target, *extra):
    p = fx.run([fx.PY, fx.SHARED / "module_facts.py", Path(repo) / target, "--format", "json", *extra])
    return p, (json.loads(p.stdout) if p.returncode == 0 else None)


class PureHelpers(unittest.TestCase):
    def test_supertypes_multiline_header(self):
        src = """class Impl(
    private val a: A,
) : Base(a), Marker<String>, Other {
}
interface Sub : Marker
"""
        got = dict(module_facts.supertypes(src))
        self.assertIn("Base", got["Impl"])
        self.assertIn("Other", got["Impl"])
        self.assertIn("Marker", got["Sub"])

    def test_strip_comments_drops_blank_and_comment_lines(self):
        text = "/* block\n comment */\n// line\n\n# py\ncode()\n  indented()\n"
        self.assertEqual(module_facts.strip_comments(text), "code()\n  indented()")

    def test_enclosing_writer_finds_function(self):
        src = "fun other() {\n}\nfun save(x: Int) {\n  db.insert()\n}\n"
        self.assertEqual(module_facts.enclosing_writer(src, 4), "save")

    def test_lang_of(self):
        self.assertEqual(module_facts.lang_of(Path("A.kt")), "kotlin")
        self.assertEqual(module_facts.lang_of(Path("a.py")), "python")
        self.assertEqual(module_facts.lang_of(Path("a.xyz")), "other")


class KotlinModule(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = fx.init_repo()
        fx.commit(cls.repo, "init", FILES)
        cls.proc, cls.f = facts(cls.repo, PKG_DIR)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.repo, ignore_errors=True)

    def decl(self, name):
        (d,) = [d for d in self.f["declarations"] if d["name"] == name]
        return d

    def test_ok(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertEqual(self.f["targets"], [PKG_DIR])

    def test_size_and_packages(self):
        self.assertEqual(self.f["files"], 2)
        self.assertEqual(self.f["packages"], ["com.acme.offers"])
        # non-blank, non-comment lines
        self.assertEqual(self.f["loc_by_file"][f"{PKG_DIR}/OfferRepository.kt"], 12)
        self.assertEqual(self.f["loc_by_file"][f"{PKG_DIR}/OfferService.kt"], 9)
        self.assertEqual(self.f["loc_total"], 21)

    def test_declarations(self):
        kinds = {d["name"]: d["kind"] for d in self.f["declarations"]}
        self.assertEqual(kinds, {"OfferRepository": "interface", "SqlOfferRepository": "class",
                                 "OfferService": "class", "helperFunction": "top-level fun"})

    def test_usage_counts(self):
        repo = self.decl("OfferRepository")
        self.assertEqual(repo["refs_main_outside_file"], 1)   # OfferService.kt
        self.assertEqual(repo["refs_test_files"], 2)          # both tests mention the name
        svc = self.decl("OfferService")
        self.assertEqual(svc["refs_main_outside_file"], 1)    # Billing.kt
        self.assertEqual(self.decl("helperFunction")["refs_main_outside_file"], 0)

    def test_interface_implementations(self):
        repo = self.decl("OfferRepository")
        self.assertEqual(repo["impls_main"], ["SqlOfferRepository (OfferRepository.kt)"])
        self.assertEqual(repo["impls_test"], ["<anonymous> (OfferServiceTest.kt)"])
        self.assertNotIn("impls_main", self.decl("OfferService"))

    def test_imports_and_fan_in(self):
        self.assertEqual(self.f["imports_out_external"], {"com.acme.shared": 1, "io.micronaut.http": 1})
        self.assertEqual(self.f["imports_out_internal"], {})
        self.assertEqual(self.f["fan_in"], {"com.acme.other": ["OfferService"]})

    def test_write_sites_and_shared_writers(self):
        sites = {(w["kind"], w["target"], w["in_function"]) for w in self.f["write_sites"]}
        self.assertEqual(sites, {("sql-write", "offers", "save"), ("outbound-mutation", "", "grant")})
        self.assertEqual(self.f["shared_writers"], {"offers": ["src/main/kotlin/com/acme/other/Billing.kt"]})
        self.assertEqual(self.f["write_api_callers"], {"save": ["src/main/kotlin/com/acme/other/Billing.kt"]})

    def test_contracts_and_versioned_paths(self):
        self.assertEqual(self.f["contract_artifacts"], ["api/openapi.yaml"])
        self.assertEqual(self.f["versioned_paths"], ['"/api/v2/offers"'])

    def test_tests_filtered_by_package_and_classified(self):
        (t,) = self.f["tests"]  # OtherTest mentions the name but is in another package
        self.assertEqual(t["file"], "src/test/kotlin/com/acme/offers/OfferServiceTest.kt")
        self.assertTrue(t["heavy"])
        self.assertFalse(t["property"])
        self.assertEqual(t["refs"], ["OfferRepository"])
        # `io.mockk.mockk` (two word matches) plus `mockk<OfferRepository>`
        self.assertEqual(t["mocks"], 3)

    def test_scar_candidates_counted_without_tracing(self):
        self.assertEqual(self.f["scar_candidates"], 1)
        self.assertEqual(self.f["scar_categories"], {"timing": 1, "constant": 1})

    def test_build_deps(self):
        self.assertEqual(self.f["build_file"], "build.gradle.kts")
        self.assertEqual(self.f["build_deps"], ["io.micronaut:micronaut-http-client:4.0", "com.acme:shared:1.0"])

    def test_markdown_report(self):
        p = fx.run([fx.PY, fx.SHARED / "module_facts.py", self.repo / PKG_DIR])
        self.assertEqual(p.returncode, 0, p.stderr)
        out = p.stdout
        self.assertIn("- files: 2, LOC (non-blank, non-comment): 21", out)
        self.assertIn("| OfferRepository | interface | OfferRepository.kt | 1 | 2 | 1/1: SqlOfferRepository (OfferRepository.kt) |", out)
        self.assertIn("- offers: src/main/kotlin/com/acme/other/Billing.kt", out)
        self.assertIn("## Tests referencing target", out)


class PythonModule(unittest.TestCase):
    def test_python_declarations(self):
        repo = fx.init_repo()
        self.addCleanup(shutil.rmtree, repo, True)
        fx.commit(repo, "init", {
            "pkg/service.py": "class Service:\n    pass\n\ndef make_service():\n    return Service()\n",
            "app/main.py": "from pkg.service import Service\nS = Service\n",
        })
        p, f = facts(repo, "pkg")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual({(d["name"], d["kind"]) for d in f["declarations"]},
                         {("Service", "class"), ("make_service", "top-level fun")})
        (svc,) = [d for d in f["declarations"] if d["name"] == "Service"]
        self.assertEqual(svc["refs_main_outside_file"], 1)


class Errors(unittest.TestCase):
    def test_outside_git_repo(self):
        with tempfile.TemporaryDirectory() as d:
            fx.write(d, "A.kt", "class A\n")
            p = fx.run([fx.PY, fx.SHARED / "module_facts.py", d])
        self.assertEqual(p.returncode, 2)
        self.assertIn("not inside a git repository", p.stderr)

    def test_target_with_only_tests_is_rejected(self):
        repo = fx.init_repo()
        self.addCleanup(shutil.rmtree, repo, True)
        fx.commit(repo, "init", {"src/test/kotlin/FooTest.kt": "class FooTest\n", "src/main/Main.kt": "class Main\n"})
        p, _ = facts(repo, "src/test")
        self.assertEqual(p.returncode, 2)
        self.assertIn("no tracked non-test source files", p.stderr)


if __name__ == "__main__":
    unittest.main()
