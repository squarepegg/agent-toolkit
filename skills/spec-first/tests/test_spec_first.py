#!/usr/bin/env python3
"""Self-test for spec-first's scripts: unit, integration (real git repos in a temp dir) and determinism.
SPEC_FIRST_SCRIPTS overrides the scripts dir (planted_bugs.py points it at mutants). Stdlib only.
  python3 tests/test_spec_first.py [-k name]"""
import json, os, shutil, subprocess, sys, tempfile, textwrap, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.environ.get("SPEC_FIRST_SCRIPTS") or os.path.join(HERE, "..", "scripts")
SPEC_PY, EV_PY, VD_PY, REVIEW = (os.path.join(SCRIPTS, n) for n in ("spec.py", "evidence.py", "verdict.py", "review.sh"))
TEMPLATES = os.path.join(HERE, "..", "templates")

SPEC = textwrap.dedent('''\
    # Clamp

    Status: draft

    ## Goal
    Callers can bound a number to a range.

    ## Decisions
    - D1: Invalid ranges raise ValueError (confirmed)
    - D2: Integers and floats both work (assumed)

    ## Requirements
    - R1: clamp(x, lo, hi) returns x bounded to [lo, hi]
    - R2: clamp with lo greater than hi raises ValueError

    ## Edge cases
    - E1: x equal to lo -> returns lo

    ## Out of scope
    - Vectors

    ## Constraints
    - Reference: calc.py

    ## Checks
    - C1 [R1, E1]: `python3 -m unittest -q tests.test_clamp` -> exit 0; output contains "OK"
    - C2 [R2]: `python3 -c "import calc; calc.clamp(1, 5, 2)" 2>&1 | tail -1` -> exit 0; stdout contains "ValueError"

    ## Open questions
    - (none)

    ## Deviations
    ''')

CALC_BASE = "def add(a, b):\n    return a + b\n"
CALC_DONE = CALC_BASE + textwrap.dedent('''\

    def clamp(x, lo, hi):
        if lo > hi:
            raise ValueError("lo > hi")
        return max(lo, min(x, hi))
    ''')
TEST_EXISTING = "import unittest, calc\n\nclass T(unittest.TestCase):\n    def test_add(self):\n        self.assertEqual(calc.add(1, 2), 3)\n"
TEST_CLAMP = textwrap.dedent('''\
    import unittest, calc

    class T(unittest.TestCase):
        def test_inside(self):
            self.assertEqual(calc.clamp(3, 1, 5), 3)
        def test_above(self):
            self.assertEqual(calc.clamp(9, 1, 5), 5)
        def test_equal_lo(self):
            self.assertEqual(calc.clamp(1, 1, 5), 1)
    ''')
GOOD_REPORT = textwrap.dedent('''\
    | ID | Verdict | Evidence |
    |---|---|---|
    | R1 | PASS | C1; calc.py:4-7 |
    | R2 | PASS | C2; calc.py:5 |
    | E1 | PASS | C1; tests/test_clamp.py:8 |
    ''')


def sh(cmd, cwd, env=None):
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, env=env)
    return p.returncode, p.stdout + p.stderr


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="specfirst-")
        self.repo = os.path.join(self.tmp, "repo"); os.makedirs(os.path.join(self.repo, "tests"))
        self.out = os.path.join(self.tmp, "ev")
        self.write("calc.py", CALC_BASE); self.write("tests/__init__.py", ""); self.write("tests/test_existing.py", TEST_EXISTING)
        self.git("init", "-q"); self.git("config", "user.email", "t@t"); self.git("config", "user.name", "t")
        with open(os.path.join(self.repo, ".git", "info", "exclude"), "a") as f: f.write("specs/\n__pycache__/\n")
        self.git("add", "-A"); self.git("commit", "-qm", "base")
        self.spec = os.path.join(self.repo, "specs", "clamp.md")
        self.write("specs/clamp.md", SPEC)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def write(self, rel, body):
        p = os.path.join(self.repo, rel); os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "w") as f: f.write(body)

    def read(self, p):
        with open(p) as f: return f.read()

    def git(self, *a):
        rc, out = sh(["git", *a], self.repo); self.assertEqual(rc, 0, out); return out

    def py(self, script, *a, env=None):
        return sh([sys.executable, script, *a], self.repo, env)

    def lint(self, body, *flags):
        self.write("specs/clamp.md", body); return self.py(SPEC_PY, "lint", self.spec, *flags)

    def accept(self):
        rc, out = self.py(SPEC_PY, "accept", self.spec); self.assertEqual(rc, 0, out)

    def build(self):
        self.write("calc.py", CALC_DONE); self.write("tests/test_clamp.py", TEST_CLAMP)

    def evidence(self, *a):
        return self.py(EV_PY, self.spec, "--out", self.out, *a)

    def verdict(self, report):
        p = os.path.join(self.tmp, "report.md")
        with open(p, "w") as f: f.write(report)
        return self.py(VD_PY, p, "--evidence", self.out)

    def ev_json(self):
        return json.loads(self.read(os.path.join(self.out, "evidence.json")))

    def check_status(self, cid):
        return next(c for c in self.ev_json()["checks"] if c["id"] == cid)


class Lint(Base):
    def test_lint_clean_spec_passes(self):
        rc, out = self.lint(SPEC, "--accepted"); self.assertEqual(rc, 0, out); self.assertIn("0 error(s)", out)

    def test_lint_template_is_not_acceptable(self):
        rc, out = self.lint(self.read(os.path.join(TEMPLATES, "spec.md")), "--accepted")
        self.assertEqual(rc, 1); self.assertIn("placeholder", out)

    def test_lint_missing_section(self):
        rc, out = self.lint(SPEC.replace("## Out of scope\n- Vectors\n", ""))
        self.assertEqual(rc, 1); self.assertIn("missing section '## Out of scope'", out)

    def test_lint_requirement_needs_id(self):
        rc, out = self.lint(SPEC.replace("- R2: clamp", "- clamp"))
        self.assertEqual(rc, 1); self.assertIn("is not '- R<n>", out)

    def test_lint_edge_id_in_requirements_rejected(self):
        rc, out = self.lint(SPEC.replace("- R2: clamp", "- E9: clamp"))
        self.assertEqual(rc, 1); self.assertIn("is not '- R<n>", out)

    def test_lint_decision_needs_tag(self):
        rc, out = self.lint(SPEC.replace(" (assumed)", ""))
        self.assertEqual(rc, 1); self.assertIn("(confirmed|assumed)", out)

    def test_lint_check_needs_exit_clause(self):
        rc, out = self.lint(SPEC.replace('-> exit 0; output contains "OK"', '-> output contains "OK"'))
        self.assertEqual(rc, 1); self.assertIn("needs an 'exit N' clause", out)

    def test_lint_warns_exit_only_check(self):
        rc, out = self.lint(SPEC.replace('; output contains "OK"', ""))
        self.assertEqual(rc, 0, out); self.assertIn("no output clause", out)

    def test_lint_bad_clause(self):
        rc, out = self.lint(SPEC.replace('output contains "OK"', "prints OK"))
        self.assertEqual(rc, 1); self.assertIn("cannot parse expectation", out)

    def test_lint_bad_regex(self):
        rc, out = self.lint(SPEC.replace('output contains "OK"', "stdout matches /(/"))
        self.assertEqual(rc, 1); self.assertIn("bad regex", out)

    def test_lint_unknown_cover_id(self):
        rc, out = self.lint(SPEC.replace("C2 [R2]", "C2 [R2, R7]"))
        self.assertEqual(rc, 1); self.assertIn("names unknown id R7", out)

    def test_lint_check_must_name_ids(self):
        rc, out = self.lint(SPEC.replace("C2 [R2]", "C2 []"))
        self.assertEqual(rc, 1); self.assertIn("names no requirement", out)

    def test_lint_uncovered_is_error_when_accepted(self):
        body = SPEC.replace("C1 [R1, E1]", "C1 [R1]")
        rc, out = self.lint(body); self.assertEqual(rc, 0, out); self.assertIn("warning: E1 is not covered", out)
        rc, out = self.lint(body, "--accepted"); self.assertEqual(rc, 1); self.assertIn("error: E1 is not covered", out)

    def test_lint_open_questions_block_accepted(self):
        body = SPEC.replace("## Open questions\n- (none)", "## Open questions\n- Should floats round?")
        rc, out = self.lint(body); self.assertEqual(rc, 0, out)
        rc, out = self.lint(body, "--accepted"); self.assertEqual(rc, 1); self.assertIn("open questions remain", out)

    def test_lint_placeholder_blocks_accepted(self):
        rc, out = self.lint(SPEC.replace("- Vectors", "- TBD"), "--accepted")
        self.assertEqual(rc, 1); self.assertIn("placeholder left", out)

    def test_lint_deviations_must_be_empty_on_accept(self):
        rc, out = self.lint(SPEC + "- used floor\n", "--accepted")
        self.assertEqual(rc, 1); self.assertIn("Deviations must be empty", out)

    def test_lint_warns_on_implementation_words(self):
        rc, out = self.lint(SPEC.replace("- R1: clamp(x, lo, hi) returns", "- R1: implement a helper class that returns"))
        self.assertEqual(rc, 0, out); self.assertIn("R1 may describe implementation", out)

    def test_lint_duplicate_id(self):
        rc, out = self.lint(SPEC.replace("- R2: clamp", "- R1: clamp"))
        self.assertEqual(rc, 1); self.assertIn("duplicate id R1", out)

    def test_lint_duplicate_section(self):
        rc, out = self.lint(SPEC + "\n## Goal\nagain\n")
        self.assertEqual(rc, 1); self.assertIn("appears twice", out)

    def test_lint_bad_status(self):
        rc, out = self.lint(SPEC.replace("Status: draft", "Status: done"))
        self.assertEqual(rc, 1); self.assertIn("Status must be draft or accepted", out)

    def test_lint_unreadable_exit_2(self):
        rc, out = self.py(SPEC_PY, "lint", os.path.join(self.tmp, "nope.md")); self.assertEqual(rc, 2)


class Accept(Base):
    def test_accept_writes_lock_and_status(self):
        self.accept()
        self.assertIn("Status: accepted", self.read(self.spec))
        lock = json.loads(self.read(self.spec + ".lock"))
        self.assertEqual(lock["base"], self.git("rev-parse", "HEAD").strip())
        rc, out = self.py(SPEC_PY, "check", self.spec); self.assertEqual(rc, 0, out)

    def test_accept_refuses_open_questions(self):
        self.write("specs/clamp.md", SPEC.replace("- (none)", "- Which rounding?"))
        rc, out = self.py(SPEC_PY, "accept", self.spec)
        self.assertEqual(rc, 1); self.assertFalse(os.path.exists(self.spec + ".lock"))

    def test_lock_catches_requirement_edit(self):
        self.accept()
        self.write("specs/clamp.md", self.read(self.spec).replace("raises ValueError", "returns None"))
        rc, out = self.py(SPEC_PY, "check", self.spec); self.assertEqual(rc, 1); self.assertIn("normative sections changed", out)

    def test_lock_catches_check_edit(self):
        self.accept()
        self.write("specs/clamp.md", self.read(self.spec).replace('contains "ValueError"', 'contains "Error"'))
        rc, _ = self.py(SPEC_PY, "check", self.spec); self.assertEqual(rc, 1)

    def test_lock_allows_deviations_and_whitespace(self):
        self.accept()
        self.write("specs/clamp.md", self.read(self.spec).replace("## Goal\n", "## Goal\n\n") + "- took the conservative option\n")
        rc, out = self.py(SPEC_PY, "check", self.spec); self.assertEqual(rc, 0, out)

    def test_check_without_lock(self):
        rc, out = self.py(SPEC_PY, "check", self.spec); self.assertEqual(rc, 1); self.assertIn("never accepted", out)


class Evidence(Base):
    def setUp(self):
        super().setUp(); self.accept()

    def test_evidence_passing_change(self):
        self.build(); rc, out = self.evidence()
        self.assertEqual(rc, 0, out)
        self.assertEqual([c["status"] for c in self.ev_json()["checks"]], ["PASS", "PASS"])
        self.assertEqual(self.ev_json()["guards"], [])

    def test_evidence_failing_clause(self):
        rc, out = self.evidence()   # nothing built: clamp is missing
        self.assertEqual(rc, 1)
        c = self.check_status("C2"); self.assertEqual(c["status"], "FAIL"); self.assertIn('expected stdout contains "ValueError"', c["reason"])

    def test_evidence_exit_code_compared(self):
        self.write("specs/clamp.md", SPEC.replace("`python3 -m unittest -q tests.test_clamp`", "`echo OK; exit 3`")); self.accept()
        self.build(); rc, _ = self.evidence()
        c = self.check_status("C1"); self.assertEqual(c["status"], "FAIL"); self.assertIn("exit 0 (got 3)", c["reason"])
        self.assertEqual(rc, 1)

    def test_evidence_manual_is_not_checked(self):
        self.write("specs/clamp.md", SPEC.replace("## Open questions", "- C3 [R1]: manual: run the CLI -> see: 5\n\n## Open questions")); self.accept()
        self.build(); rc, _ = self.evidence()
        self.assertEqual(self.check_status("C3")["status"], "NOT CHECKED")
        self.assertEqual(rc, 0)   # manual checks do not fail the mechanical run; the verdict keeps them NOT CHECKED

    def test_evidence_timeout_is_not_checked(self):
        self.write("specs/clamp.md", SPEC.replace("`python3 -m unittest -q tests.test_clamp`", "`sleep 5`")); self.accept()
        rc, _ = self.evidence("--timeout", "1")
        c = self.check_status("C1"); self.assertEqual(c["status"], "NOT CHECKED"); self.assertIn("timed out", c["reason"])
        self.assertEqual(rc, 1)

    def test_evidence_missing_command_is_not_checked(self):
        self.write("specs/clamp.md", SPEC.replace("`python3 -m unittest -q tests.test_clamp`", "`no-such-tool-xyz --run`")); self.accept()
        self.evidence()
        self.assertEqual(self.check_status("C1")["status"], "NOT CHECKED")

    def test_evidence_lacks_and_matches_clauses(self):
        body = SPEC.replace('output contains "OK"', 'output lacks "FAILED"; output matches /^OK$/')
        self.write("specs/clamp.md", body); self.accept(); self.build()
        rc, out = self.evidence(); self.assertEqual(rc, 0, out)
        self.write("tests/test_clamp.py", TEST_CLAMP.replace("(3, 1, 5), 3", "(3, 1, 5), 4000"))
        self.evidence(); self.assertEqual(self.check_status("C1")["status"], "FAIL")

    def test_evidence_guard_existing_test_modified(self):
        self.build(); self.write("tests/test_existing.py", TEST_EXISTING.replace("3)", "3)  # loosened"))
        rc, _ = self.evidence(); self.assertEqual(rc, 1)
        self.assertIn("existing test modified: tests/test_existing.py", self.ev_json()["guards"])

    def test_evidence_guard_existing_test_deleted(self):
        self.build(); os.remove(os.path.join(self.repo, "tests/test_existing.py"))
        self.evidence(); self.assertIn("existing test deleted: tests/test_existing.py", self.ev_json()["guards"])

    def test_evidence_new_test_is_not_a_guard(self):
        self.build(); self.evidence(); self.assertEqual(self.ev_json()["files"].get("tests/test_clamp.py"), "A")
        self.assertEqual(self.ev_json()["guards"], [])

    def test_evidence_custom_test_globs(self):
        self.write("specs/clamp.md", SPEC.replace("- Reference: calc.py", "- Reference: calc.py\n- Tests: checks/**")); self.accept()
        self.build(); self.write("checks/t.py", "x"); self.git("add", "checks/t.py"); self.git("commit", "-qm", "c")
        self.write("specs/clamp.md", self.read(self.spec)); self.accept()
        self.write("checks/t.py", "y"); self.write("tests/test_existing.py", TEST_EXISTING + "#\n")
        self.evidence(); g = self.ev_json()["guards"]
        self.assertIn("existing test modified: checks/t.py", g); self.assertNotIn("existing test modified: tests/test_existing.py", g)

    def test_evidence_guard_spec_changed(self):
        self.build(); self.write("specs/clamp.md", self.read(self.spec).replace("raises ValueError", "returns lo"))
        rc, _ = self.evidence(); self.assertEqual(rc, 1)
        self.assertTrue(any("normative sections changed" in g for g in self.ev_json()["guards"]))

    def test_evidence_out_inside_repo_refused(self):
        rc, out = self.py(EV_PY, self.spec, "--out", os.path.join(self.repo, "ev"))
        self.assertEqual(rc, 2); self.assertIn("inside the repository", out)

    def test_evidence_no_lock_no_base_refused(self):
        os.remove(self.spec + ".lock"); rc, out = self.evidence(); self.assertEqual(rc, 2); self.assertIn("no --base", out)

    def test_evidence_untracked_files_in_diff(self):
        self.build(); self.evidence()
        patch = self.read(os.path.join(self.out, "diff.patch"))
        self.assertIn("+++ b/tests/test_clamp.py (untracked)", patch); self.assertIn("+def clamp", patch)
        self.assertIn("A\ttests/test_clamp.py", self.read(os.path.join(self.out, "files.txt")))
        self.assertNotIn("specs/clamp.md", self.read(os.path.join(self.out, "files.txt")))

    def test_evidence_md_has_literal_output(self):
        self.build(); self.evidence(); md = self.read(os.path.join(self.out, "evidence.md"))
        self.assertIn("## C1 [R1, E1]: PASS", md); self.assertIn("Ran 3 tests", md)

    def test_evidence_is_deterministic(self):
        self.build(); self.evidence()
        first = {n: self.read(os.path.join(self.out, n)) for n in ("evidence.json", "evidence.md", "diff.patch", "files.txt")}
        shutil.rmtree(self.out); self.evidence()
        for n, body in first.items(): self.assertEqual(self.read(os.path.join(self.out, n)), body, n)

    def test_evidence_does_not_touch_repo(self):
        self.build(); before = self.git("status", "--porcelain", "--untracked-files=all")
        self.evidence(); self.assertEqual(self.git("status", "--porcelain", "--untracked-files=all"), before)


class Verdict(Base):
    def setUp(self):
        super().setUp(); self.accept(); self.build(); rc, out = self.evidence(); self.assertEqual(rc, 0, out)

    def test_verdict_good_report_is_done(self):
        rc, out = self.verdict(GOOD_REPORT); self.assertEqual(rc, 0, out); self.assertIn("STATUS: DONE (PASS 3)", out)

    def test_verdict_rubber_stamp_is_unsupported(self):
        rc, out = self.verdict(GOOD_REPORT.replace("C2; calc.py:5", "the tests pass, looks great"))
        self.assertEqual(rc, 1); self.assertIn("| R2 | UNSUPPORTED |", out)

    def test_verdict_pass_on_failed_check_is_fail(self):
        self.write("calc.py", CALC_DONE.replace('raise ValueError("lo > hi")', "return lo")); self.evidence()
        rc, out = self.verdict(GOOD_REPORT); self.assertEqual(rc, 1); self.assertIn("| R2 | FAIL | C2 failed |", out)

    def test_verdict_manual_check_keeps_not_checked(self):
        self.write("specs/clamp.md", SPEC.replace("## Open questions", "- C3 [R2]: manual: run it -> see: error\n\n## Open questions")); self.accept()
        self.evidence(); rc, out = self.verdict(GOOD_REPORT)
        self.assertEqual(rc, 1); self.assertIn("| R2 | NOT CHECKED | C3 did not run |", out)

    def test_verdict_check_must_cover_id(self):
        rc, out = self.verdict(GOOD_REPORT.replace("C2; calc.py:5", "C1"))
        self.assertIn("| R2 | UNSUPPORTED |", out); self.assertIn("C1 does not cover R2", out)

    def test_verdict_unknown_check_id(self):
        rc, out = self.verdict(GOOD_REPORT.replace("C2; calc.py:5", "C9"))
        self.assertIn("C9 does not exist", out); self.assertEqual(rc, 1)

    def test_verdict_line_out_of_range(self):
        rc, out = self.verdict(GOOD_REPORT.replace("C2; calc.py:5", "calc.py:99"))
        self.assertIn("line out of range", out); self.assertEqual(rc, 1)

    def test_verdict_untouched_file_not_evidence(self):
        rc, out = self.verdict(GOOD_REPORT.replace("C2; calc.py:5", "tests/test_existing.py:5"))
        self.assertIn("not a file the change touched", out); self.assertEqual(rc, 1)

    def test_verdict_edge_case_needs_test_file(self):
        rc, out = self.verdict(GOOD_REPORT.replace("C1; tests/test_clamp.py:8", "calc.py:7"))
        self.assertIn("an edge case needs a test", out); self.assertEqual(rc, 1)

    def test_verdict_edge_case_test_file_citation_passes(self):
        rc, out = self.verdict(GOOD_REPORT.replace("C1; tests/test_clamp.py:8", "tests/test_clamp.py:8"))
        self.assertEqual(rc, 0, out)

    def test_verdict_missing_row(self):
        rc, out = self.verdict(GOOD_REPORT.replace("| E1 | PASS | C1; tests/test_clamp.py:8 |\n", ""))
        self.assertEqual(rc, 1); self.assertIn("| E1 | MISSING |", out)

    def test_verdict_duplicate_row_is_bad_input(self):
        rc, out = self.verdict(GOOD_REPORT + "| R1 | FAIL | x |\n"); self.assertEqual(rc, 2); self.assertIn("two rows for R1", out)

    def test_verdict_unknown_id_is_bad_input(self):
        rc, out = self.verdict(GOOD_REPORT + "| R9 | PASS | C1 |\n"); self.assertEqual(rc, 2)

    def test_verdict_reviewer_fail_is_kept(self):
        rc, out = self.verdict(GOOD_REPORT.replace("| R1 | PASS | C1; calc.py:4-7 |", "| R1 | FAIL | floats are truncated at calc.py:7 |"))
        self.assertEqual(rc, 1); self.assertIn("| R1 | FAIL | reviewer:", out)

    def test_verdict_reviewer_not_checked_is_kept(self):
        rc, out = self.verdict(GOOD_REPORT.replace("| R1 | PASS | C1; calc.py:4-7 |", "| R1 | NOT CHECKED | needs a device |"))
        self.assertEqual(rc, 1); self.assertIn("| R1 | NOT CHECKED |", out)

    def test_verdict_guard_blocks_done(self):
        self.write("tests/test_existing.py", TEST_EXISTING + "# edited\n"); self.evidence()
        rc, out = self.verdict(GOOD_REPORT); self.assertEqual(rc, 1); self.assertIn("BLOCKED: existing test modified", out)

    def test_verdict_stale_tree_blocks_done(self):
        self.write("calc.py", CALC_DONE + "\n# later edit\n")
        rc, out = self.verdict(GOOD_REPORT); self.assertEqual(rc, 1); self.assertIn("STALE: the working tree changed", out)

    def test_verdict_stale_head_blocks_done(self):
        self.git("add", "-A"); self.git("commit", "-qm", "x")
        rc, out = self.verdict(GOOD_REPORT); self.assertEqual(rc, 1); self.assertIn("STALE: HEAD moved", out)

    def test_verdict_stale_spec_blocks_done(self):
        self.write("specs/clamp.md", self.read(self.spec).replace("- Vectors", "- Vectors and matrices"))
        rc, out = self.verdict(GOOD_REPORT); self.assertEqual(rc, 1); self.assertIn("STALE: the spec changed", out)

    def test_verdict_ignores_prose_and_lowercase_verdicts_count(self):
        rc, out = self.verdict("Great work overall!\n" + GOOD_REPORT.replace("| R1 | PASS |", "| R1 | pass |") + "\nAll good.\n")
        self.assertEqual(rc, 0, out)

    def test_verdict_is_deterministic(self):
        a = self.verdict(GOOD_REPORT.replace("C2; calc.py:5", "nope")); b = self.verdict(GOOD_REPORT.replace("C2; calc.py:5", "nope"))
        self.assertEqual(a, b)


class Review(Base):
    def setUp(self):
        super().setUp(); self.accept(); self.build(); self.evidence()
        self.fake = os.path.join(self.tmp, "fake.sh")

    def runner(self, body):
        with open(self.fake, "w") as f: f.write("#!/bin/bash\n" + body)
        os.chmod(self.fake, 0o755)
        return dict(os.environ, SPEC_REVIEW_RUNNER=self.fake)

    def review(self, env):
        return sh(["bash", REVIEW, self.spec, self.out], self.repo, env)

    def test_review_brief_holds_only_spec_evidence_diff(self):
        env = self.runner(f"cat > {self.tmp}/seen.md\ncat <<'R'\n{GOOD_REPORT}R\n")
        rc, out = self.review(env); self.assertEqual(rc, 0, out); self.assertIn("STATUS: DONE", out)
        seen = self.read(os.path.join(self.tmp, "seen.md"))
        self.assertIn("You review a change that you did not write", seen)
        self.assertIn("- R2: clamp with lo greater than hi", seen); self.assertIn("## C1 [R1, E1]: PASS", seen); self.assertIn("+def clamp", seen)
        self.assertEqual(seen, self.read(os.path.join(self.out, "brief-1.md")))
        self.assertTrue(os.path.exists(os.path.join(self.out, "verdict-1.md")))

    def test_review_reviewer_edit_voids_report(self):
        env = self.runner(f"echo hacked >> calc.py\ncat <<'R'\n{GOOD_REPORT}R\n")
        rc, out = self.review(env); self.assertEqual(rc, 4); self.assertIn("report is void", out)
        self.assertFalse(os.path.exists(os.path.join(self.out, "review-1.md")))

    def test_review_round_limit(self):
        env = self.runner(f"cat <<'R'\n{GOOD_REPORT}R\n")
        for _ in range(3): self.review(env)
        rc, out = self.review(env); self.assertEqual(rc, 3); self.assertIn("3 rounds done", out)

    def test_review_rubber_stamp_runner_not_done(self):
        env = self.runner("echo 'Looks great, all requirements pass!'\n")
        rc, out = self.review(env); self.assertEqual(rc, 1); self.assertIn("STATUS: NOT DONE (MISSING 3)", out)

    def fake_cli(self, names):
        bindir = os.path.join(self.tmp, "bin"); os.makedirs(bindir, exist_ok=True)
        for name in names:
            with open(os.path.join(bindir, name), "w") as f: f.write(f"#!/bin/bash\necho \"{name} $*\" > {self.tmp}/argv\ncat >/dev/null\n")
            os.chmod(os.path.join(bindir, name), 0o755)
        for tool in ("bash", "git", "python3", "find", "wc", "tr", "cat", "mv", "sed", "dirname", "printf"):
            p = shutil.which(tool)
            if p and not os.path.exists(os.path.join(bindir, tool)): os.symlink(p, os.path.join(bindir, tool))
        env = dict(os.environ, PATH=bindir); env.pop("SPEC_REVIEW_RUNNER", None)
        return env

    def test_review_runner_prefers_pi(self):
        self.review(self.fake_cli(["pi", "claude"]))
        self.assertIn("pi -p --no-session --no-context-files --no-skills --tools read", self.read(os.path.join(self.tmp, "argv")))

    def test_review_runner_falls_back_to_claude_read_only(self):
        self.review(self.fake_cli(["claude"]))
        argv = self.read(os.path.join(self.tmp, "argv"))
        self.assertIn("claude -p --allowedTools Read Grep Glob", argv); self.assertIn("--disallowedTools Bash Edit Write", argv)

    def test_review_runner_missing_is_refused(self):
        rc, out = self.review(self.fake_cli([])); self.assertEqual(rc, 2); self.assertIn("no reviewer CLI", out)

    def test_review_needs_evidence(self):
        shutil.rmtree(self.out); rc, out = self.review(os.environ.copy()); self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main(verbosity=1)
