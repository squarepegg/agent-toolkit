#!/usr/bin/env python3
"""`sdlc.sh consolidate --round N` end to end with a fake role (SDLC_RUNNER_consolidate): the role's proposals are
validated by consolidate.py, only accepted rules are staged, nothing reaches the project, the step is idempotent, and
failures (no proposals, a symlinked rule, no snapshot, nothing reproduced, switched off) are reported, not hidden.
No Gradle, no Docker: the sandbox is off and the role is a shell script."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SDLC = ROOT / "scripts/sdlc.sh"
RULE = "id: eval-call\\nlanguage: python\\nseverity: error\\nmessage: no eval\\nrule:\\n  pattern: eval($X)\\n"

FAKE_OK = f"""cat > /dev/null
mkdir -p consolidate/rules
printf '{RULE}' > consolidate/rules/eval-call.yml
cat > consolidate/proposals.json <<'J'
{{"proposals": [{{"finding": "F1", "kind": "rule", "rule_file": "eval-call.yml", "reason": "eval on input"}},
               {{"finding": "F2", "kind": "none", "reason": "semantics"}}]}}
J
"""
FAKE_NONE = "cat > /dev/null\n"
FAKE_SYMLINK = """cat > /dev/null
mkdir -p consolidate/rules
ln -s /etc/passwd consolidate/rules/eval-call.yml
cat > consolidate/proposals.json <<'J'
{"proposals": [{"finding": "F1", "kind": "rule", "rule_file": "eval-call.yml", "reason": "x"},
               {"finding": "F2", "kind": "none", "reason": "y"}]}
J
"""


class ConsolidateRun(unittest.TestCase):
    def setUp(self) -> None:
        self.d = Path(tempfile.mkdtemp())
        self.p = self.d / "proj"
        (self.p / "src/main").mkdir(parents=True)
        (self.p / "src/main/m.py").write_text("def f(s):\n    return int(s)\n")  # the fixed code
        run = self.p / ".sdlc"
        r1 = run / "attack/round-1"
        (r1 / "snapshot-pre/src/main").mkdir(parents=True)
        (r1 / "snapshot-pre/src/main/m.py").write_text("def f(s):\n    return eval(s)\n")  # the code that was attacked
        (run / "spec.md").write_text("# spec\n")
        staged = r1 / "staged_F1.py"
        staged.write_text("# repro\n")
        self.findings = {"findings": [
            {"id": "F1", "status": "reproduced", "title": "eval on input", "staged": str(staged), "spec_refs": ["INV-1"]},
            {"id": "F2", "status": "reproduced", "title": "other", "spec_refs": []},
            {"id": "F3", "status": "discarded", "title": "no"}]}
        (r1 / "findings.validated.json").write_text(json.dumps(self.findings))
        (self.p / "gate.config").write_text('GATE_MAIN_PATHS="src/main"\nGATE_TEST_INVENTORY="tests-inventory.json"\nGATE_MAIN_PACKAGES="demo"\n')
        (self.p / "sdlc.config").write_text(f'SDLC_SANDBOX=off\nSDLC_RUNNER_consolidate="bash {self.p}/fake_role.sh"\n')
        self.set_role(FAKE_OK)

    def set_role(self, body: str) -> None:
        (self.p / "fake_role.sh").write_text(body)

    def sdlc(self, *args: str, **env: str) -> subprocess.CompletedProcess:
        e = {**os.environ, "SDLC_TEST_NO_NOTIFY": "1", **env}
        return subprocess.run(["bash", str(SDLC), "--project", str(self.p), *args], capture_output=True, text=True, env=e,
                              timeout=120, cwd=self.p, check=False)

    def report(self) -> dict:
        return json.loads((self.p / ".sdlc/consolidate/round-1/report.json").read_text())

    def roles_run(self) -> int:
        f = self.p / ".sdlc/roles.jsonl"
        return len(f.read_text().splitlines()) if f.exists() else 0

    def project_tree(self) -> dict[str, str]:
        return {str(x.relative_to(self.p)): x.read_text() for x in sorted(self.p.rglob("*"))
                if x.is_file() and ".sdlc" not in x.parts and "fake_role.sh" not in x.name}

    def test_accepted_rule_is_staged_and_the_project_is_untouched(self) -> None:
        before = self.project_tree()
        r = self.sdlc("consolidate", "--round", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = self.report()
        self.assertEqual((rep["reproduced"], rep["rules_accepted"], rep["rules_refused"], rep["problems"]), (2, 1, 0, []))
        self.assertEqual(rep["by_kind"], {"rule": 1, "behaviour": 0, "none": 1})
        self.assertTrue((self.p / ".sdlc/consolidate/round-1/staged-rules/eval-call.yml").is_file())
        self.assertEqual(self.project_tree(), before)  # nothing was adopted: no rules dir, no config change
        self.assertFalse((self.p / "rules").exists())
        self.assertEqual(self.roles_run(), 1)

    def test_role_runs_are_tagged_with_their_round(self) -> None:
        self.sdlc("consolidate", "--round", "1")
        rec = json.loads((self.p / ".sdlc/roles.jsonl").read_text().splitlines()[-1])
        self.assertEqual((rec["role"], rec["round"]), ("consolidate", 1))

    def test_the_role_sees_only_its_inputs_and_only_reproduced_findings(self) -> None:
        self.set_role("cat > /dev/null\nls -1 > consolidate/seen.txt\nls -1 pre/src/main fixed/src/main consolidate/tests >> consolidate/seen.txt\n"
                      "cp consolidate/findings.json consolidate/findings.seen.json\n"
                      "mkdir -p consolidate/rules; echo '{\"proposals\": []}' > consolidate/proposals.json\n")
        self.sdlc("consolidate", "--round", "1")
        out = self.p / ".sdlc/consolidate/round-1/role-out/consolidate"
        top = (out / "seen.txt").read_text().split()
        self.assertEqual(sorted(top[:5]), ["SPEC.md", "TASK.md", "consolidate", "fixed", "pre"])  # no project, no .sdlc
        self.assertIn("m.py", top[5:])
        self.assertIn("staged_F1.py", top)  # the failing test of the reproduced finding
        seen = json.loads((out / "findings.seen.json").read_text())
        self.assertEqual([f["id"] for f in seen["findings"]], ["F1", "F2"])  # F3 was discarded
        self.assertEqual(seen["findings"][0]["test"], "tests/staged_F1.py")
        self.assertNotIn("test", seen["findings"][1])

    def test_second_run_is_a_noop(self) -> None:
        self.sdlc("consolidate", "--round", "1")
        first = (self.p / ".sdlc/consolidate/round-1/report.json").read_text()
        r = self.sdlc("consolidate", "--round", "1")
        self.assertEqual(r.returncode, 0)
        self.assertIn("done earlier", r.stdout + r.stderr)
        self.assertEqual(self.roles_run(), 1)
        self.assertEqual((self.p / ".sdlc/consolidate/round-1/report.json").read_text(), first)

    def test_no_proposals_is_a_reported_failure_without_a_report(self) -> None:
        self.set_role(FAKE_NONE)
        r = self.sdlc("consolidate", "--round", "1")
        self.assertNotEqual(r.returncode, 0)
        self.assertIn("could not be validated", r.stdout + r.stderr)
        self.assertFalse((self.p / ".sdlc/consolidate/round-1/report.json").exists())
        self.assertFalse((self.p / ".sdlc/consolidate/round-1/staged-rules").exists())

    def test_a_symlinked_rule_file_is_never_followed(self) -> None:
        self.set_role(FAKE_SYMLINK)
        r = self.sdlc("consolidate", "--round", "1")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        rep = self.report()
        self.assertEqual((rep["rules_accepted"], rep["rules_refused"]), (0, 1))
        self.assertFalse((self.p / ".sdlc/consolidate/round-1/staged-rules").exists())

    def test_switched_off_nothing_reproduced_and_no_snapshot_do_nothing(self) -> None:
        cfg = self.p / "sdlc.config"
        base = cfg.read_text()
        cfg.write_text(base + "SDLC_CONSOLIDATE=off\n")
        self.assertEqual(self.sdlc("consolidate", "--round", "1").returncode, 0)
        self.assertFalse((self.p / ".sdlc/consolidate").exists())
        self.assertEqual(self.roles_run(), 0)
        cfg.write_text(base + "SDLC_CONSOLIDATE=on\n")
        shutil.rmtree(self.p / ".sdlc/attack/round-1/snapshot-pre")
        r = self.sdlc("consolidate", "--round", "1")
        self.assertIn("no pre-fix snapshot", r.stdout + r.stderr)
        self.assertEqual(self.roles_run(), 0)
        for f in self.findings["findings"]:
            f["status"] = "discarded"
        (self.p / ".sdlc/attack/round-1/findings.validated.json").write_text(json.dumps(self.findings))
        self.assertEqual(self.sdlc("consolidate", "--round", "1").returncode, 0)
        self.assertEqual(self.roles_run(), 0)

    def test_usage_error(self) -> None:
        self.assertNotEqual(self.sdlc("consolidate").returncode, 0)


if __name__ == "__main__":
    unittest.main()
