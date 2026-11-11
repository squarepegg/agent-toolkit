"""`sdlc.sh adopt-rules` end to end: human only, one atomic batch, files locked, audit trail, nothing changed on refusal."""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDLC = ROOT / "scripts/sdlc.sh"
TOOL = ROOT / "scripts/sdlc_tool.py"
RULE = "id: eval-call\nlanguage: python\nseverity: error\nmessage: no eval\nrule:\n  pattern: eval($X)\n"


class AdoptRun(unittest.TestCase):
    def setUp(self) -> None:
        self.p = Path(tempfile.mkdtemp()) / "proj"
        (self.p / "src/main").mkdir(parents=True)
        (self.p / "src/main/m.py").write_text("def f(s):\n    return int(s)\n")
        (self.p / "gate.config").write_text('GATE_MAIN_PATHS="src/main"\nGATE_TEST_INVENTORY="inv.json"\nGATE_MAIN_PACKAGES="demo"\n')
        (self.p / "sdlc.config").write_text("SDLC_SANDBOX=off\n")
        d = self.p / ".sdlc/consolidate/round-1/staged-rules"
        d.mkdir(parents=True)
        (d / "eval-call.yml").write_text(RULE)
        subprocess.run([sys.executable, str(TOOL), "lock", str(self.p), str(self.p / ".sdlc/tests.lock"), "--spec-sha", "x"],
                       check=True, capture_output=True)

    def sdlc(self, *args: str, stdin: str | None = None) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SDLC), "--project", str(self.p), *args], capture_output=True, text=True,
                              env={**os.environ}, timeout=120, cwd=self.p, check=False, input=stdin or "")

    def lock_files(self) -> dict:
        return json.loads((self.p / ".sdlc/tests.lock").read_text())["files"]

    def snapshot(self) -> tuple:
        return ((self.p / "gate.config").read_text(), sorted(self.lock_files()), (self.p / "sdlc-rules").exists())

    def test_relayed_adoption_copies_locks_configures_and_leaves_a_trail(self) -> None:
        before = self.lock_files()
        r = self.sdlc("adopt-rules", "--relayed", "user: adopt the batch")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual((self.p / "sdlc-rules/eval-call.yml").read_text(), RULE)
        self.assertIn('GATE_RULES="sdlc-rules"', (self.p / "gate.config").read_text())
        after = self.lock_files()
        self.assertIn("sdlc-rules/eval-call.yml", after)
        self.assertNotIn("sdlc-rules/eval-call.yml", before)
        self.assertNotEqual(after["gate.config"], before["gate.config"])  # the changed config is locked again
        hist = [json.loads(x) for x in (self.p / ".sdlc/lock-history.jsonl").read_text().splitlines()]
        self.assertTrue(any("adopt rules (relayed by agent): user: adopt the batch" in h["reason"] for h in hist))
        self.assertIn("adopted rules (relayed by agent): user: adopt the batch", (self.p / ".sdlc/violations.log").read_text())
        self.assertTrue((self.p / ".sdlc/consolidate/adopted.jsonl").is_file())

    def test_without_a_human_nothing_changes(self) -> None:
        before = self.snapshot()
        r = self.sdlc("adopt-rules")  # no terminal, no --relayed
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.snapshot(), before)
        r = self.sdlc("adopt-rules", "--relayed", "")  # empty words
        self.assertNotEqual(r.returncode, 0)
        self.assertEqual(self.snapshot(), before)

    def test_dry_run_needs_no_human_and_changes_nothing(self) -> None:
        before = self.snapshot()
        r = self.sdlc("adopt-rules", "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn('"adopted": [\n    "eval-call.yml"', r.stdout)
        self.assertEqual(self.snapshot(), before)

    def test_a_refused_batch_changes_nothing_and_says_why(self) -> None:
        (self.p / "src/main/m.py").write_text("eval('1')\n")
        before = self.snapshot()
        r = self.sdlc("adopt-rules", "--relayed", "go")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("matches the current main sources", r.stdout)
        self.assertIn("nothing was changed", r.stdout + r.stderr)
        self.assertEqual(self.snapshot(), before)

    def test_adopting_twice_and_naming_a_rule(self) -> None:
        self.assertEqual(self.sdlc("adopt-rules", "eval-call.yml", "--relayed", "go").returncode, 0)
        r = self.sdlc("adopt-rules", "--relayed", "again")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("nothing new to adopt", r.stdout + r.stderr)

    def test_needs_a_lock(self) -> None:
        (self.p / ".sdlc/tests.lock").unlink()
        r = self.sdlc("adopt-rules", "--relayed", "go")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("no tests.lock", r.stdout + r.stderr)
        self.assertFalse((self.p / "sdlc-rules").exists())


if __name__ == "__main__":
    unittest.main()
