"""B4: the attacker is told which classes the gate already checks mechanically (rule id and message, not the matcher),
only when rules are adopted; the round's identity changes when that list does, and stays the same when there is none.
Also checks B1's pre-fix snapshot is taken for the round. Fake attacker, no Gradle, no Docker."""
from __future__ import annotations

import hashlib
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
RULE = "id: eval-call\nlanguage: python\nseverity: error\nmessage: Do not call eval on input;\n  parse it instead.\nrule:\n  pattern: eval($X)\n"
RULE2 = "id: exec-call\nlanguage: python\nseverity: error\nmessage: 'No exec.'\nrule:\n  pattern: exec($X)\n"
FAKE = """cat > /dev/null
mkdir -p attack "$CAPTURE"
cp attack/TASK.md "$CAPTURE/TASK.md"
[ -f attack/GATED.md ] && cp attack/GATED.md "$CAPTURE/GATED.md"
find . -type f | sort > "$CAPTURE/files.txt"
echo '{"findings": [], "attempted": ["x"]}' > attack/findings.json
"""


class AttackRound(unittest.TestCase):
    def setUp(self) -> None:
        d = Path(tempfile.mkdtemp())
        self.p, self.cap = d / "proj", d / "cap"
        (self.p / "src/main").mkdir(parents=True)
        (self.p / "src/main/m.py").write_text("def f(s):\n    return int(s)\n")
        (self.p / "gate.config").write_text('GATE_MAIN_PATHS="src/main"\nGATE_TEST_INVENTORY="inv.json"\nGATE_MAIN_PACKAGES="demo"\n')
        (self.p / "sdlc.config").write_text(f'SDLC_SANDBOX=off\nSDLC_REFEREE=off\nSDLC_RUNNER_attack="bash {d}/fake.sh"\n')
        (d / "fake.sh").write_text(FAKE)
        run = self.p / ".sdlc"
        run.mkdir()
        (run / "spec.md").write_text("# spec\n")
        sha = hashlib.sha256((run / "spec.md").read_bytes()).hexdigest()
        subprocess.run([sys.executable, str(TOOL), "lock", str(self.p), str(run / "tests.lock"), "--spec-sha", sha],
                       check=True, capture_output=True)
        self.sdlc("approve", "--relayed", "ok")

    def sdlc(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["bash", str(SDLC), "--project", str(self.p), *args], capture_output=True, text=True,
                              env={**os.environ, "CAPTURE": str(self.cap)}, timeout=300, cwd=self.p, check=False)

    def cfg_of(self, rnd: int) -> str:
        return json.loads((self.p / f".sdlc/attack/round-{rnd}/attacker.done").read_text())["attacker_cfg"]

    def test_no_rules_no_list_and_the_snapshot_is_taken(self) -> None:
        r = self.sdlc("attack", "--round", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse((self.cap / "GATED.md").exists())
        self.assertNotIn("GATED.md", (self.cap / "TASK.md").read_text())
        rec = json.loads((self.p / ".sdlc/roles.jsonl").read_text().splitlines()[-1])
        self.assertEqual((rec["role"], rec["round"]), ("attack", 1))
        self.assertEqual((self.p / ".sdlc/attack/round-1/snapshot-pre/src/main/m.py").read_text(),
                         (self.p / "src/main/m.py").read_text())

    def test_adopted_rules_are_listed_to_the_attacker_without_their_matchers(self) -> None:
        none_cfg = (self.sdlc("attack", "--round", "1"), self.cfg_of(1))[1]
        (self.p / "sdlc-rules").mkdir()
        (self.p / "sdlc-rules/eval-call.yml").write_text(RULE)
        (self.p / "gate.config").write_text((self.p / "gate.config").read_text() + 'GATE_RULES="sdlc-rules"\n')
        r = self.sdlc("attack", "--round", "2")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        gated = (self.cap / "GATED.md").read_text()
        self.assertIn("- `eval-call`: Do not call eval on input; parse it instead.", gated)
        self.assertNotIn("eval($X)", gated)
        self.assertIn("attack/GATED.md", (self.cap / "TASK.md").read_text())
        files = (self.cap / "files.txt").read_text()
        self.assertNotIn("sdlc-rules", files)  # the rule files themselves are not in the attacker's workspace
        self.assertNotEqual(self.cfg_of(2), none_cfg)  # a different brief is a different round identity
        (self.p / "sdlc-rules/exec-call.yml").write_text(RULE2)
        self.sdlc("attack", "--round", "3")
        self.assertNotEqual(self.cfg_of(3), self.cfg_of(2))  # and so is a different list
        self.sdlc("attack", "--round", "4")
        self.assertEqual(self.cfg_of(4), self.cfg_of(3))  # the same list is the same identity


if __name__ == "__main__":
    unittest.main()
