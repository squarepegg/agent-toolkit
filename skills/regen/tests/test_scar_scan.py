"""Tests for _shared/scripts/scar_scan.py (pure helpers and an end-to-end run on a tempdir git repo).

Run: python3 skills/regen/tests/test_scar_scan.py
"""
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixtures as fx  # noqa: E402

sys.path.insert(0, str(fx.SHARED))
import scar_scan  # noqa: E402

CLIENT = "src/main/kotlin/com/acme/offers/VendorClient.kt"

V1 = """package com.acme.offers

class VendorClient {
    fun send(payload: String): String {
        return payload
    }
}
"""

V2 = """package com.acme.offers

class VendorClient {
    // Retry three times: the vendor dropped connections during the outage (INC-1234).
    val maxAttempts = 3

    fun send(payload: String): String {
        return payload
    }
}
"""

# a later, unrelated commit touches a different line (adds a broad catch)
V3 = """package com.acme.offers

class VendorClient {
    // Retry three times: the vendor dropped connections during the outage (INC-1234).
    val maxAttempts = 3

    fun send(payload: String): String {
        try {
            return payload
        } catch (e: Exception) {
            return ""
        }
    }
}
"""


def scan_json(repo, *extra):
    p = fx.run([fx.PY, fx.SHARED / "scar_scan.py", repo, "--pr", "off", "--format", "json", *extra])
    return p, (json.loads(p.stdout) if p.returncode == 0 else None)


class PureHelpers(unittest.TestCase):
    def test_find_pr_variants(self):
        self.assertEqual(scar_scan.find_pr("Fix retry (#42)"), "42")
        self.assertEqual(scar_scan.find_pr("Merge pull request #7 from x/y"), "7")
        self.assertEqual(scar_scan.find_pr("see https://example.com/org/repo/pull/19"), "19")
        self.assertIsNone(scar_scan.find_pr("no pr here, issue 5"))

    def test_find_incidents(self):
        refs = scar_scan.find_incidents("Retry after outage INC-1234; ticket ABC-77 also SEV-2")
        self.assertIn("outage", refs)
        self.assertIn("INC-1234", refs)
        self.assertIn("ABC-77", refs)
        self.assertIn("SEV-2", refs)
        self.assertEqual(scar_scan.find_incidents("Rename variable for clarity"), [])

    def test_find_incidents_dedupes_case_insensitively(self):
        self.assertEqual(scar_scan.find_incidents("Outage then outage again"), ["Outage"])

    def test_parse_log_round_trip(self):
        rec = scar_scan.FIELD.join(["a" * 40, "Ann", "2024-02-03T10:00:00+00:00",
                                    "Fix timeout after incident (#9)", "root cause: slow dns"]) + scar_scan.REC
        (c,) = scar_scan.parse_log(rec + "\n")
        self.assertEqual((c.sha, c.author, c.date, c.pr), ("a" * 10, "Ann", "2024-02-03", "9"))
        self.assertIn("incident", c.incident_refs)
        self.assertIn("root cause", c.incident_refs)

    def test_parse_log_skips_malformed(self):
        self.assertEqual(scar_scan.parse_log("garbage" + scar_scan.REC), [])

    def test_preceding_comment_joins_block_and_trailing(self):
        lines = ["", "// first line", "// second line", "@Suppress(\"x\")", "val a = 1  // tail note"]
        self.assertEqual(scar_scan.preceding_comment(lines, 4), "first line second line tail note")

    def test_preceding_comment_stops_at_blank(self):
        lines = ["// unrelated", "", "val a = 1"]
        self.assertEqual(scar_scan.preceding_comment(lines, 2), "")

    def test_tokens_prefers_unique_whole_line(self):
        text = "val maxAttempts = 3\nval other = 4\n"
        toks = scar_scan.tokens_for("val maxAttempts = 3", text)
        self.assertEqual(toks[0], "val maxAttempts = 3")
        self.assertIn("maxAttempts", toks)

    def test_merge_sites_collapses_adjacent_same_category(self):
        def cand(line, cat):
            return scar_scan.Candidate(file="a.kt", line=line, categories=[cat], priority=1, code="x")
        merged = scar_scan.merge_sites([cand(10, "retry"), cand(11, "retry"), cand(12, "retry"),
                                        cand(40, "retry"), cand(41, "timing")])
        self.assertEqual([(c.line, c.end_line) for c in merged], [(10, 12), (40, 40), (41, 41)])

    def test_merge_sites_caps_site_span(self):
        cands = [scar_scan.Candidate(file="a.kt", line=n, categories=["retry"], priority=1, code="x")
                 for n in range(10, 31, 2)]
        merged = scar_scan.merge_sites(cands)
        self.assertEqual([(c.line, c.end_line) for c in merged], [(10, 24), (26, 30)])

    def test_merge_sites_keeps_different_categories_apart(self):
        a = scar_scan.Candidate(file="a.kt", line=1, categories=["retry"], priority=1, code="x")
        b = scar_scan.Candidate(file="a.kt", line=2, categories=["marker"], priority=0, code="y")
        self.assertEqual(len(scar_scan.merge_sites([a, b])), 2)

    def test_md_escape(self):
        self.assertEqual(scar_scan.md_escape("a|b\nc"), "a\\|b c")


class PatternScan(unittest.TestCase):
    """scan() on plain files: categories per line, no git history needed beyond `git init`."""

    def setUp(self):
        self.repo = fx.init_repo()
        self.addCleanup(lambda: __import__("shutil").rmtree(self.repo, ignore_errors=True))

    def cats(self, rel, text, include_tests=False):
        fx.write(self.repo, rel, text)
        cands = scar_scan.scan([self.repo], self.repo, include_tests)
        return {c.code: c.categories for c in cands}

    def test_categories(self):
        got = self.cats("src/Main.kt", """package a
const val CONNECT_TIMEOUT_MS = 2500
try { run() } catch (e: Exception) { log(e) }
if (status == "LEGACY_VENDOR") skip()
val maxRetries = 5
@Suppress("UNCHECKED_CAST")
""")
        self.assertIn("timing", got["const val CONNECT_TIMEOUT_MS = 2500"])
        self.assertIn("constant", got["const val CONNECT_TIMEOUT_MS = 2500"])
        self.assertIn("broad-catch", got["try { run() } catch (e: Exception) { log(e) }"])
        self.assertIn("special-case", got['if (status == "LEGACY_VENDOR") skip()'])
        self.assertIn("retry", got["val maxRetries = 5"])
        self.assertIn("suppression", got['@Suppress("UNCHECKED_CAST")'])

    def test_plain_code_has_no_candidates(self):
        self.assertEqual(self.cats("src/Plain.kt", "package a\nfun add(a: Int, b: Int) = a + b\n"), {})

    def test_comment_marker_only_kept_above_code(self):
        got = self.cats("src/M.py", "# workaround for vendor bug\nvalue = compute()\n\n# hack: trailing\n")
        self.assertEqual(list(got), ["# workaround for vendor bug"])

    def test_config_files_scanned(self):
        got = self.cats("src/main/resources/application.yml", "client:\n  read-timeout: 30\n  name: x\n")
        self.assertEqual(got, {"read-timeout: 30": ["timing"]})

    def test_tests_skipped_unless_included(self):
        body = "val maxRetries = 5\n"
        fx.write(self.repo, "src/test/kotlin/FooTest.kt", body)
        self.assertEqual(scar_scan.scan([self.repo], self.repo, False), [])
        self.assertEqual(len(scar_scan.scan([self.repo], self.repo, True)), 1)

    def test_skip_dirs_ignored(self):
        fx.write(self.repo, "build/generated/Gen.kt", "val maxRetries = 5\n")
        fx.write(self.repo, "node_modules/x/index.js", "setTimeout(f, 5000)\n")
        self.assertEqual(scar_scan.scan([self.repo], self.repo, False), [])


class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.repo = fx.init_repo()
        fx.commit(cls.repo, "Initial client", {CLIENT: V1})
        fx.commit(cls.repo, "Retry vendor calls after outage INC-1234 (#42)\n\n"
                            "Vendor dropped connections during the outage.", {CLIENT: V2})
        fx.commit(cls.repo, "Swallow errors in send", {CLIENT: V3})
        fx.commit(cls.repo, "Cover retries", {
            "src/test/kotlin/com/acme/offers/VendorClientRetryTest.kt":
                "package com.acme.offers\n\nclass VendorClientRetryTest { fun t() { VendorClient().maxAttempts } }\n"})
        cls.proc, cls.data = scan_json(cls.repo)

    @classmethod
    def tearDownClass(cls):
        __import__("shutil").rmtree(cls.repo, ignore_errors=True)

    def by_code(self, needle):
        (c,) = [c for c in self.data["candidates"] if needle in c["code"]]
        return c

    def test_exit_ok_and_envelope(self):
        self.assertEqual(self.proc.returncode, 0, self.proc.stderr)
        self.assertEqual(self.data["untraced"], 0)
        self.assertFalse(self.data["pr_lookup"])

    def test_retry_after_incident_traced_to_origin_commit(self):
        c = self.by_code("maxAttempts")
        self.assertIn("retry", c["categories"])
        self.assertEqual(c["nearby_comment"],
                         "Retry three times: the vendor dropped connections during the outage (INC-1234).")
        o = c["origin"]
        self.assertEqual(o["subject"], "Retry vendor calls after outage INC-1234 (#42)")
        self.assertEqual(o["pr"], "42")
        self.assertIn("INC-1234", o["incident_refs"])
        self.assertIn("outage", o["incident_refs"])
        self.assertTrue(c["origin_method"].startswith("git log -S"))

    def test_origin_is_not_last_touch_when_later_commit_edits_neighbours(self):
        # the catch commit changed other lines; maxAttempts must still point at the retry commit
        c = self.by_code("maxAttempts")
        self.assertEqual(c["origin"]["subject"].split(" (")[0], "Retry vendor calls after outage INC-1234")

    def test_broad_catch_traced_to_later_commit(self):
        c = self.by_code("catch (e: Exception)")
        self.assertIn("broad-catch", c["categories"])
        self.assertEqual(c["origin"]["subject"], "Swallow errors in send")
        self.assertIsNone(c["origin"]["pr"])
        self.assertEqual(c["origin"]["incident_refs"], [])

    def test_tests_mentioning(self):
        c = self.by_code("maxAttempts")
        self.assertEqual(c["tests_mentioning"], ["src/test/kotlin/com/acme/offers/VendorClientRetryTest.kt"])

    def test_markdown_report(self):
        p = fx.run([fx.PY, fx.SHARED / "scar_scan.py", self.repo, "--pr", "off"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue(p.stdout.startswith("# scar_scan: "))
        self.assertIn("val maxAttempts = 3", p.stdout)
        self.assertIn("Retry vendor calls after outage INC-1234 (#42)", p.stdout)
        self.assertIn("#42", p.stdout)
        self.assertIn("INC-1234", p.stdout)

    def test_max_limits_traced_candidates(self):
        _, data = scan_json(self.repo, "--max", "1")
        self.assertEqual(len(data["candidates"]), 1)
        self.assertEqual(data["untraced"], len(self.data["candidates"]) - 1)

    def test_path_outside_git_is_exit_2(self):
        with tempfile.TemporaryDirectory() as d:
            fx.write(d, "A.kt", "val maxRetries = 3\n")
            p = fx.run([fx.PY, fx.SHARED / "scar_scan.py", d, "--pr", "off"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("not inside a git repository", p.stderr)

    def test_missing_path_is_exit_2(self):
        p = fx.run([fx.PY, fx.SHARED / "scar_scan.py", self.repo / "nope", "--pr", "off"])
        self.assertEqual(p.returncode, 2)
        self.assertIn("does not exist", p.stderr)


class MovedCode(unittest.TestCase):
    """A line extracted into a new file is traced to the commit that wrote it originally."""

    def test_move_traced_through_file_creation(self):
        repo = fx.init_repo()
        self.addCleanup(lambda: __import__("shutil").rmtree(repo, ignore_errors=True))
        line = '    val pageFetchBackoffMillis = 750 // keep: vendor rate limits at 800ms'
        fx.commit(repo, "Add paging", {"src/Big.kt": f"class Big {{\n{line}\n}}\n"})
        fx.commit(repo, "Backoff for vendor 429s after incident INC-77 (#5)",
                  {"src/Big.kt": f"class Big {{\n{line}\n    val other = 1\n}}\n"})
        # the new file is mostly new content so `git log --follow` cannot see through the extraction
        filler = "".join(f"    fun f{i}() = {i * 7919}\n" for i in range(30))
        fx.commit(repo, "Extract Paging", {"src/Big.kt": "class Big {\n    val other = 1\n}\n",
                                           "src/Paging.kt": "class Paging {\n" + filler + line + "\n}\n"})
        _, data = scan_json(repo / "src" / "Paging.kt")
        (c,) = [c for c in data["candidates"] if "pageFetchBackoffMillis" in c["code"]]
        self.assertEqual(c["origin"]["subject"], "Add paging")
        self.assertIn("pre-move", c["origin_method"])
        self.assertTrue(any("moved into this file" in n for n in c["notes"]), c["notes"])

        _, data = scan_json(repo / "src" / "Paging.kt", "--no-repo-wide")
        (c,) = [c for c in data["candidates"] if "pageFetchBackoffMillis" in c["code"]]
        self.assertEqual(c["origin"]["subject"], "Extract Paging")
        self.assertTrue(any("--no-repo-wide" in n for n in c["notes"]), c["notes"])


if __name__ == "__main__":
    unittest.main()
