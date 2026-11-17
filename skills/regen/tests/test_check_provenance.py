"""Tests for architecture-constraints/scripts/check_provenance.py.

Run: python3 skills/regen/tests/test_check_provenance.py
"""
import json
import shutil
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixtures as fx  # noqa: E402

AC = fx.REGEN / "architecture-constraints"
SCRIPT = AC / "scripts" / "check_provenance.py"
sys.path.insert(0, str(AC / "scripts"))
import check_provenance as cp  # noqa: E402

FIELDS = {
    "Rule": "only the writer module touches cooldown state.",
    "Reason": "cooldown state has one logical writer; a second would race the read-modify-write.\n"
              "     *   It also corrupts window counters.",
    "Set by": "platform team, 2026-05-21",
    "Source": "ADR 0004",
    "Revisit when": "purchases need to write the same table.",
    "Regeneration test": "yes - a regenerated service writing directly would double count.",
}


def header(omit=(), **override):
    fields = dict(FIELDS, **{k.replace("_", " "): v for k, v in override.items()})
    lines = [f"     * {k}: {v}" for k, v in fields.items() if k not in omit]
    return "    /**\n     * @fitness\n" + "\n".join(lines) + "\n     */\n"


HEADER = header()

RULE = """    private val onlyWriterWritesCooldown: ArchRule =
        noClasses().that().resideOutsideOfPackages("a..").should().dependOnClassesThat().haveFullyQualifiedName("b.C")
"""

KT_PREFIX = "package a\n\nimport com.tngtech.archunit.lang.ArchRule\n\nclass T {\n"


def kt(body):
    return KT_PREFIX + body + "}\n"


class Parse(unittest.TestCase):
    def test_parse_doc_collects_fields_and_continuations(self):
        fields = cp.parse_doc(HEADER)
        self.assertEqual(set(fields), set(cp.FIELDS))
        self.assertEqual(fields["Rule"], "only the writer module touches cooldown state.")
        self.assertTrue(fields["Reason"].endswith("It also corrupts window counters."))
        self.assertEqual(fields["Set by"], "platform team, 2026-05-21")

    def test_parse_doc_blank_line_ends_continuation(self):
        fields = cp.parse_doc("Rule: a\n\nstray text\nReason: b\n")
        self.assertEqual(fields, {"Rule": "a", "Reason": "b"})

    def test_empty_values(self):
        for v in ("", "TODO", "tbd", "unknown", "?", "n/a", "-", "Unknown."):
            self.assertTrue(cp.EMPTY.match(v), v)
        for v in ("unknown threat model", "incident INC-12", "todo list rotation"):
            self.assertFalse(cp.EMPTY.match(v), v)


class Statuses(unittest.TestCase):
    def setUp(self):
        self.dir = fx.tempdir()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def rules(self, text, name="T.kt"):
        return cp.rules_in(fx.write(self.dir, name, text))

    def test_complete_header_is_ok(self):
        (r,) = self.rules(kt(HEADER + RULE))
        self.assertEqual((r["rule"], r["status"], r["missing"], r["disabled"]),
                         ("onlyWriterWritesCooldown", "ok", [], False))
        self.assertIn(r["line"], (15, 16))  # the closing */ line or the declaration line

    def test_missing_header_is_enforced_without_reason(self):
        (r,) = self.rules(kt(RULE))
        self.assertEqual(r["status"], "ENFORCED WITHOUT REASON")
        self.assertEqual(r["missing"], cp.FIELDS)

    def test_missing_reason_field_only(self):
        (r,) = self.rules(kt(header(omit=("Reason",)) + RULE))
        self.assertEqual(r["status"], "ENFORCED WITHOUT REASON")
        self.assertEqual(r["missing"], ["Reason"])

    def test_reason_present_but_other_field_missing_is_incomplete(self):
        (r,) = self.rules(kt(header(omit=("Revisit when",)) + RULE))
        self.assertEqual(r["status"], "incomplete header")
        self.assertEqual(r["missing"], ["Revisit when"])

    def test_placeholder_values_count_as_missing(self):
        for placeholder in ("unknown", "TODO", "tbd", "N/A"):
            (r,) = self.rules(kt(header(Source=placeholder) + RULE))
            self.assertEqual(r["missing"], ["Source"], placeholder)

    def test_disabled_rule_without_reason_is_flagged_not_enforced(self):
        (r,) = self.rules(kt(header(Reason="unknown") + '    @Disabled("no provenance")\n' + RULE))
        self.assertEqual(r["status"], "flagged (disabled, reason missing)")
        self.assertTrue(r["disabled"])

    def test_disabled_annotation_with_parens_in_string(self):
        (r,) = self.rules(kt(header(Reason="unknown") +
                             '    @Disabled("fails today (Controller -> Client)")\n' + RULE))
        self.assertTrue(r["disabled"])

    def test_disabled_does_not_leak_from_neighbouring_rule(self):
        second = RULE.replace("onlyWriterWritesCooldown", "second")
        text = kt('    @Disabled("other")\n    fun unrelated() {}\n\n' + HEADER + RULE + "\n" + second)
        r1, r2 = self.rules(text)
        self.assertEqual((r1["status"], r1["disabled"]), ("ok", False))
        self.assertEqual(r2["status"], "ENFORCED WITHOUT REASON")
        self.assertFalse(r2["disabled"])

    def test_two_rules_reported_separately(self):
        second = header(Source="unknown") + RULE.replace("onlyWriterWritesCooldown", "second")
        rs = self.rules(kt(HEADER + RULE + "\n" + second))
        self.assertEqual([(r["rule"], r["status"]) for r in rs],
                         [("onlyWriterWritesCooldown", "ok"), ("second", "incomplete header")])

    def test_fitness_tagged_test_function(self):
        src = kt(HEADER + '    @Test\n    fun `vendor timeout stays below the balancer idle timeout`() {}\n')
        (r,) = self.rules(src)
        self.assertEqual(r["rule"], "vendor timeout stays below the balancer idle timeout")
        self.assertEqual(r["status"], "ok")

    def test_java_arch_test_annotation(self):
        src = ("import com.tngtech.archunit.junit.ArchTest;\nclass T {\n"
               "  @ArchTest\n  static final ArchRule noCycles = slices().matching(\"a.(*)..\").should().beFreeOfCycles();\n}\n")
        rs = self.rules(src, "T.java")
        self.assertTrue(rs)
        self.assertEqual({r["status"] for r in rs}, {"ENFORCED WITHOUT REASON"})

    def test_python_docstring_fitness(self):
        src = ('def test_cooldown_writer_is_unique():\n    """\n    @fitness\n'
               "    Rule: one writer.\n    Reason: races.\n    Set by: team\n    Source: ADR 1\n"
               "    Revisit when: never needed again.\n    Regeneration test: yes - counters break\n"
               '    """\n    assert True\n')
        (r,) = self.rules(src, "test_arch.py")
        # a docstring is detected via its @fitness tag and its header parsed
        self.assertEqual(r["status"], "ok")

    def test_files_without_archunit_or_fitness_are_ignored(self):
        self.assertEqual(self.rules("class A { val noClasses = 1 }\n"), [])

    def test_non_arch_declarations_are_not_reported(self):
        self.assertEqual(self.rules(kt("    fun helper() = 1\n")), [])


class Cli(unittest.TestCase):
    def setUp(self):
        self.dir = fx.tempdir()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def cli(self, *args):
        return fx.run([fx.PY, SCRIPT, *args])

    def test_strict_passes_on_valid_header(self):
        fx.write(self.dir, "src/ArchTest.kt", kt(HEADER + RULE))
        p = self.cli(self.dir, "--strict")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("1 fitness functions", p.stdout)
        self.assertRegex(p.stdout, r"\| ArchTest\.kt:1[56] \| onlyWriterWritesCooldown \| ok \| - \|")

    def test_strict_fails_on_missing_header(self):
        fx.write(self.dir, "src/ArchTest.kt", kt(RULE))
        p = self.cli(self.dir, "--strict")
        self.assertEqual(p.returncode, 1)
        self.assertIn("ENFORCED WITHOUT REASON", p.stdout)
        self.assertIn("Reason", p.stdout)

    def test_strict_fails_on_incomplete_header_but_not_without_flag(self):
        fx.write(self.dir, "ArchTest.kt", kt(header(omit=("Source",)) + RULE))
        self.assertEqual(self.cli(self.dir, "--strict").returncode, 1)
        self.assertEqual(self.cli(self.dir).returncode, 0)

    def test_strict_passes_when_bad_rule_is_disabled(self):
        fx.write(self.dir, "ArchTest.kt", kt('    @Disabled("flagged")\n' + RULE))
        p = self.cli(self.dir, "--strict")
        self.assertEqual(p.returncode, 0)
        self.assertIn("flagged (disabled, reason missing)", p.stdout)

    def test_json_format(self):
        fx.write(self.dir, "ArchTest.kt", kt(HEADER + RULE))
        data = json.loads(self.cli(self.dir, "--format", "json").stdout)
        self.assertEqual([(d["rule"], d["status"]) for d in data], [("onlyWriterWritesCooldown", "ok")])
        self.assertEqual(data[0]["fields"]["Set by"], "platform team, 2026-05-21")

    def test_skipped_directories_and_extensions(self):
        fx.write(self.dir, "build/ArchTest.kt", kt(RULE))
        fx.write(self.dir, "node_modules/ArchTest.kt", kt(RULE))
        fx.write(self.dir, "notes.md", "import com.tngtech.archunit @fitness")
        p = self.cli(self.dir, "--strict")
        self.assertEqual(p.returncode, 0)
        self.assertIn("No ArchUnit rules or @fitness-tagged tests found.", p.stdout)

    def test_single_file_argument(self):
        f = fx.write(self.dir, "Only.kt", kt(HEADER + RULE))
        fx.write(self.dir, "Other.kt", kt(RULE))
        p = self.cli(f, "--strict")
        self.assertEqual(p.returncode, 0)
        self.assertIn("1 fitness functions", p.stdout)

    def test_shipped_examples_pass_strict(self):
        p = self.cli(AC / "examples", "--strict")
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn("8 fitness functions", p.stdout)
        self.assertEqual(p.stdout.count("| ok |"), 7)
        self.assertEqual(p.stdout.count("flagged (disabled, reason missing)"), 1)


if __name__ == "__main__":
    unittest.main()
