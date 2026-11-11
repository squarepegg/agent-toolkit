"""B4 (unit): the attacker-facing list of gated classes: rule id and message, never the matcher, only for real rules."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("adopt_rules", ROOT / "scripts/adopt_rules.py")
if _s is None or _s.loader is None:
    raise RuntimeError("cannot load adopt_rules.py")
ar = importlib.util.module_from_spec(_s)
_s.loader.exec_module(ar)

RULE = "id: eval-call\nlanguage: python\nseverity: error\nmessage: Do not call eval on input;\n  parse it instead.\nrule:\n  pattern: eval($X)\n"
RULE2 = "id: exec-call\nlanguage: python\nseverity: error\nmessage: 'No exec.'\nrule:\n  pattern: exec($X)\n"

class Lists(unittest.TestCase):
    def test_message_forms(self) -> None:
        self.assertEqual(ar.rule_message(RULE), "Do not call eval on input; parse it instead.")
        self.assertEqual(ar.rule_message(RULE2), "No exec.")
        self.assertEqual(ar.rule_message("id: x\nmessage: >\n  folded\n  text\nrule: {}\n"), "folded text")
        self.assertEqual(ar.rule_message("id: x\nrule: {}\n"), "")

    def test_list_contents_and_exclusions(self) -> None:
        d = Path(tempfile.mkdtemp())
        self.assertEqual(ar.gated_classes(d), "")  # no rules: nothing
        self.assertEqual(ar.gated_classes(d / "absent"), "")
        (d / "b.yml").write_text(RULE2)
        (d / "a.yml").write_text(RULE)
        (d / "noid.yml").write_text("language: python\n")
        (d / "notes.txt").write_text("id: sneaky")
        (d / "link.yml").symlink_to(d / "a.yml")
        out = ar.gated_classes(d)
        self.assertLess(out.index("eval-call"), out.index("exec-call"))  # sorted by file name
        self.assertEqual(out.count("- `"), 2)  # no id, not .yml, and the symlink are left out
        self.assertIn("- `eval-call`: Do not call eval on input; parse it instead.", out)
        self.assertNotIn("eval($X)", out)  # the matcher is not shown
        self.assertNotIn("sneaky", out)

    def test_cli(self) -> None:
        d = Path(tempfile.mkdtemp())
        (d / "a.yml").write_text(RULE)
        r = subprocess.run([sys.executable, str(ROOT / "scripts/adopt_rules.py"), "--gated-classes",
                            str(d)], capture_output=True, text=True, check=False)
        self.assertEqual((r.returncode, r.stdout), (0, ar.gated_classes(d)))
        r = subprocess.run([sys.executable, str(ROOT / "scripts/adopt_rules.py")], capture_output=True,
                           text=True, check=False)
        self.assertEqual(r.returncode, 2)  # --project and --run are still required otherwise


if __name__ == "__main__":
    unittest.main()
