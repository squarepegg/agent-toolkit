"""Tests for decision-capture: new_decision.py, the commit-msg hook and install_hook.sh.

Run: python3 skills/regen/tests/test_decision_capture.py
"""
import datetime as dt
import os
import shutil
import stat
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import _fixtures as fx  # noqa: E402

DC = fx.REGEN / "decision-capture"
NEW = DC / "scripts" / "new_decision.py"
HOOK = DC / "scripts" / "commit-msg"
INSTALL = DC / "scripts" / "install_hook.sh"
EXAMPLE = DC / "examples" / "0004-send-only-triggers-to-vendor.md"

sys.path.insert(0, str(DC / "scripts"))
import new_decision  # noqa: E402

TODAY = dt.date.today().isoformat()

GOOD = """# 0001: Title

- **Status:** Accepted

## Context
Facts.

## Options considered
A or B.

## Decision
A.

## Rejected alternatives
B: slower.

## Assumptions
Traffic stays low.

## What would make this wrong
p99 above 2s.
"""


class RepoCase(unittest.TestCase):
    def setUp(self):
        self.repo = fx.init_repo()
        fx.git(self.repo, "config", "user.name", "Dana Decider")
        self.addCleanup(shutil.rmtree, self.repo, True)

    def new(self, *args):
        return fx.run([fx.PY, NEW, "new", *args, "--repo", self.repo])


class NewDecision(RepoCase):
    def test_empty_repo_starts_at_0001_in_docs_decisions(self):
        p = self.new("Use Postgres for offers")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout.strip(), "docs/decisions/0001-use-postgres-for-offers.md")
        self.assertIn("Decision: docs/decisions/0001-use-postgres-for-offers.md", p.stderr)
        text = (self.repo / p.stdout.strip()).read_text()
        self.assertTrue(text.startswith("# 0001: Use Postgres for offers\n"))
        self.assertIn("- **Status:** Proposed", text)
        self.assertIn(f"- **Date:** {TODAY}", text)
        self.assertIn("- **Deciders:** Dana Decider", text)
        self.assertNotIn("{{", text)

    def test_numbering_continues_from_highest_even_with_gaps(self):
        fx.write(self.repo, "docs/decisions/0003-a.md", "x")
        fx.write(self.repo, "docs/decisions/0007-b.md", "x")
        p = self.new("Next One")
        self.assertEqual(p.stdout.strip(), "docs/decisions/0008-next-one.md")

    def test_prefers_first_existing_candidate_dir_and_dir_override(self):
        fx.write(self.repo, "docs/adr/0010-old.md", "x")
        self.assertEqual(self.new("Another").stdout.strip(), "docs/adr/0011-another.md")
        self.assertEqual(self.new("Third", "--dir", "adr").stdout.strip(), "adr/0001-third.md")

    def test_dated_scheme_detected_from_existing_files(self):
        fx.write(self.repo, "docs/decisions/2026-05-11-first.md", "x")
        p = self.new("Second Thing")
        self.assertEqual(p.stdout.strip(), f"docs/decisions/{TODAY}-second-thing.md")
        self.assertIn(f"# {TODAY}: Second Thing", (self.repo / p.stdout.strip()).read_text())

    def test_slugify(self):
        self.assertEqual(new_decision.slugify("  Send ONLY triggers -- to Vendor!! "), "send-only-triggers-to-vendor")
        self.assertEqual(new_decision.slugify("a" * 100), "a" * 60)
        self.assertEqual(new_decision.slugify("Ünïcode & symbols"), "n-code-symbols")

    def test_status_flag(self):
        p = self.new("Accept me", "--status", "Accepted")
        self.assertIn("- **Status:** Accepted", (self.repo / p.stdout.strip()).read_text())

    def test_dry_run_writes_nothing(self):
        p = self.new("Dry", "--dry-run")
        self.assertEqual(p.returncode, 0)
        self.assertIn("# would write docs/decisions/0001-dry.md", p.stdout)
        self.assertIn("# 0001: Dry", p.stdout)
        self.assertFalse((self.repo / "docs").exists())

    def test_refuses_to_overwrite_existing_record(self):
        fx.write(self.repo, "docs/decisions/2026-05-11-seed.md", "x")
        fx.write(self.repo, f"docs/decisions/{TODAY}-same.md", "keep me")
        p = self.new("Same")
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("exists", p.stderr)
        self.assertEqual((self.repo / f"docs/decisions/{TODAY}-same.md").read_text(), "keep me")

    def test_refuses_gitignored_directory(self):
        fx.write(self.repo, ".gitignore", "docs/decisions/\n")
        p = self.new("Hidden")
        self.assertEqual(p.returncode, 2)
        self.assertIn("gitignored", p.stderr)
        self.assertFalse((self.repo / "docs/decisions").exists())

    def test_allow_ignored_writes_anyway(self):
        fx.write(self.repo, ".gitignore", "docs/decisions/\n")
        p = self.new("Hidden", "--allow-ignored")
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertTrue((self.repo / "docs/decisions/0001-hidden.md").is_file())

    def test_outside_git_repo(self):
        with tempfile.TemporaryDirectory() as d:
            p = fx.run([fx.PY, NEW, "new", "X", "--repo", d])
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not inside a git repository", p.stderr)

    def test_generated_record_has_empty_sections_until_filled(self):
        p = self.new("Fresh")
        c = fx.run([fx.PY, NEW, "check", self.repo / p.stdout.strip()])
        self.assertEqual(c.returncode, 1)
        for sec in new_decision.REQUIRED:
            self.assertIn(f"empty section: {sec}", c.stdout)


class CheckAndList(RepoCase):
    def test_shipped_example_passes_check(self):
        p = fx.run([fx.PY, NEW, "check", EXAMPLE])
        self.assertEqual(p.returncode, 0, p.stdout)
        self.assertIn(": ok", p.stdout)

    def test_check_flags_missing_empty_and_long(self):
        p = fx.write(self.repo, "a.md", GOOD.replace("## Assumptions\nTraffic stays low.\n\n", "")
                     .replace("A or B.", "TODO"))
        probs = new_decision.check_file(p)
        self.assertIn("missing section: Assumptions", probs)
        self.assertIn("empty section: Options considered", probs)
        self.assertEqual(len(probs), 2)
        long = fx.write(self.repo, "b.md", GOOD + "\n" * 70)
        self.assertTrue(any("keep under 60" in x for x in new_decision.check_file(long)))
        self.assertEqual(new_decision.check_file(fx.write(self.repo, "c.md", GOOD)), [])

    def test_check_exit_code_is_nonzero_if_any_file_bad(self):
        ok = fx.write(self.repo, "ok.md", GOOD)
        bad = fx.write(self.repo, "bad.md", "# nothing\n")
        p = fx.run([fx.PY, NEW, "check", ok, bad])
        self.assertEqual(p.returncode, 1)
        self.assertIn("ok.md: ok", p.stdout)
        self.assertIn("bad.md: missing section: Context", p.stdout)

    def test_list_shows_status_and_linked_commits(self):
        rec = "docs/decisions/0001-alpha.md"
        fx.commit(self.repo, "add record", {rec: GOOD.replace("Accepted", "Accepted (2026)")})
        fx.write(self.repo, "docs/decisions/0002-beta.md", "# 0002\n- **Status:** Proposed\n")
        fx.commit(self.repo, f"Implement alpha\n\nDecision: {rec}", {"x.txt": "1"})
        fx.commit(self.repo, "Unrelated mention of Decision: elsewhere", {"y.txt": "1"})
        sha = fx.git(self.repo, "log", "-1", "--format=%h", "--grep=Implement alpha").stdout.strip()
        p = fx.run([fx.PY, NEW, "list", "--repo", self.repo])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn(f"| {rec} | Accepted (2026) | {sha} |", p.stdout)
        self.assertIn("| docs/decisions/0002-beta.md | Proposed | none |", p.stdout)

    def test_list_links_by_bare_id(self):
        fx.commit(self.repo, "add", {"docs/decisions/0001-alpha.md": GOOD})
        fx.commit(self.repo, "Do it\n\nDecision: 0001", {"x.txt": "1"})
        sha = fx.git(self.repo, "log", "-1", "--format=%h").stdout.strip()
        p = fx.run([fx.PY, NEW, "list", "--repo", self.repo])
        self.assertIn(f"| {sha} |", p.stdout)

    def test_list_without_records(self):
        p = fx.run([fx.PY, NEW, "list", "--repo", self.repo])
        self.assertEqual(p.stdout.strip(), "no decision records")


class CommitMsgHook(RepoCase):
    def hook(self, message, strict=False):
        msg = fx.write(self.repo, ".git/COMMIT_EDITMSG", message)
        return fx.run([fx.PY, HOOK, msg], cwd=self.repo,
                      extra_env={"PHOENIX_DECISION_STRICT": "1"} if strict else None)

    def record(self, name="0007-use-queue.md", stage=True):
        fx.write(self.repo, f"docs/decisions/{name}", GOOD)
        if stage:
            fx.git(self.repo, "add", f"docs/decisions/{name}")

    def test_plain_message_passes_silently(self):
        p = self.hook("Fix typo\n")
        self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_numeric_trailer_resolves_to_existing_record(self):
        self.record(stage=False)
        p = self.hook("Add queue\n\nDecision: 0007\n", strict=True)
        self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_path_trailer_resolves(self):
        self.record(stage=False)
        p = self.hook("Add queue\n\nDecision: docs/decisions/0007-use-queue.md\n", strict=True)
        self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_dated_id_resolves(self):
        self.record("2026-05-11-use-queue.md", stage=False)
        p = self.hook("Add queue\n\nDecision: 2026-05-11\n", strict=True)
        self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_unresolved_trailer_warns_by_default(self):
        p = self.hook("Add queue\n\nDecision: 0042\n")
        self.assertEqual(p.returncode, 0)
        self.assertIn("warning (decision-capture)", p.stderr)
        self.assertIn("Decision: 0042 does not resolve", p.stderr)

    def test_unresolved_trailer_blocks_when_strict(self):
        p = self.hook("Add queue\n\nDecision: 0042\n", strict=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("error (decision-capture)", p.stderr)

    def test_new_record_without_trailer_is_reported(self):
        self.record()
        p = self.hook("Add queue\n", strict=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("new decision record docs/decisions/0007-use-queue.md is not linked", p.stderr)
        self.assertIn("Decision: docs/decisions/0007-use-queue.md", p.stderr)

    def test_new_record_with_matching_trailer_passes(self):
        self.record()
        p = self.hook("Add queue\n\nDecision: 0007\n", strict=True)
        self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_trailer_resolving_to_staged_record_passes(self):
        self.record()
        os.remove(self.repo / "docs/decisions/0007-use-queue.md")  # only in the index
        p = self.hook("Add queue\n\nDecision: 0007\n", strict=True)
        self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_gitignored_target_is_flagged(self):
        fx.write(self.repo, ".gitignore", "docs/decisions/\n")
        self.record(stage=False)
        p = self.hook("Add queue\n\nDecision: 0007\n", strict=True)
        self.assertEqual(p.returncode, 1)
        self.assertIn("gitignored", p.stderr)

    def test_commented_trailer_lines_are_ignored(self):
        p = self.hook("Add queue\n# Decision: 0042\n", strict=True)
        self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_trailer_must_be_on_its_own_line(self):
        p = self.hook("Add queue, see Decision: 0042 for details\n", strict=True)
        self.assertEqual((p.returncode, p.stderr), (0, ""))

    def test_merge_fixup_squash_revert_are_skipped(self):
        for subject in ("Merge branch 'x'", "fixup! thing", "squash! thing", "amend! thing", 'Revert "thing"'):
            p = self.hook(f"{subject}\n\nDecision: 0042\n", strict=True)
            self.assertEqual((p.returncode, p.stderr), (0, ""), subject)


class InstallHook(RepoCase):
    def install(self, *args):
        return fx.run(["sh", INSTALL, self.repo, *args])

    def dest(self):
        return self.repo / ".git" / "hooks" / "commit-msg"

    def test_installs_executable_copy(self):
        p = self.install()
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("Installed", p.stdout)
        self.assertEqual(self.dest().read_text(), HOOK.read_text())
        self.assertTrue(self.dest().stat().st_mode & stat.S_IXUSR)

    def test_second_run_does_not_overwrite(self):
        self.install()
        self.dest().write_text("#!/bin/sh\n# local edits\n")
        p = self.install()
        self.assertEqual(p.returncode, 0)
        self.assertIn("already exists", p.stdout)
        self.assertEqual(self.dest().read_text(), "#!/bin/sh\n# local edits\n")

    def test_existing_foreign_hook_is_kept_and_chaining_hint_printed(self):
        self.dest().parent.mkdir(parents=True, exist_ok=True)
        self.dest().write_text("#!/bin/sh\necho mine\n")
        p = self.install()
        self.assertIn(f'python3 "{HOOK}"', p.stdout)
        self.assertEqual(self.dest().read_text(), "#!/bin/sh\necho mine\n")

    def test_hooks_path_is_respected(self):
        fx.git(self.repo, "config", "core.hooksPath", ".husky")
        p = self.install()
        self.assertEqual(p.returncode, 0)
        self.assertIn("core.hooksPath is set to '.husky'", p.stdout)
        self.assertFalse(self.dest().exists())
        self.assertEqual(fx.git(self.repo, "config", "core.hooksPath").stdout.strip(), ".husky")

    def test_not_a_git_repo(self):
        with tempfile.TemporaryDirectory() as d:
            p = fx.run(["sh", INSTALL, d])
        self.assertEqual(p.returncode, 2)
        self.assertIn("not a git repository", p.stderr)

    def test_installed_hook_runs_during_real_commit(self):
        self.install()
        fx.write(self.repo, "docs/decisions/0001-x.md", GOOD)
        fx.git(self.repo, "add", "-A")
        p = fx.git(self.repo, "commit", "-m", "Add record", check=False,
                   extra_env={"PHOENIX_DECISION_STRICT": "1"})
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("not linked", p.stderr)
        p = fx.git(self.repo, "commit", "-m", "Add record\n\nDecision: 0001", check=False,
                   extra_env={"PHOENIX_DECISION_STRICT": "1"})
        self.assertEqual(p.returncode, 0, p.stderr)


if __name__ == "__main__":
    unittest.main()
