#!/usr/bin/env python3
"""A sandboxed run that did not complete (sandbox failure 97, time limit, kill) must never discard a finding or a
planted bug: it is "incomplete". Found on Phase 5 run 2 (2026-11-05): an expired AWS SSO session made every repro and
mutant check exit 97, and the harness filed them as "no-build", discarding a finding and skipping calibration."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sdlc_tool", ROOT / "scripts/sdlc_tool.py")
assert _spec is not None and _spec.loader is not None
t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t)


def unsandboxed(cmd, rw, deny):
    return cmd


class Incomplete(unittest.TestCase):
    def setUp(self) -> None:
        self.d = Path(tempfile.mkdtemp())

    def test_a_failed_sandbox_is_incomplete_not_no_build(self) -> None:
        for code in (97, 124, 137, 143, -9):
            with self.subTest(code), mock.patch.object(t, "sandboxed", unsandboxed), \
                    mock.patch.object(t, "run_cmd", return_value=code):
                self.assertEqual(t.run_repro_in(self.d, ["attack.round1.F1"], self.d / "log", 10), ("incomplete", []))

    def test_a_completed_run_without_reports_is_still_no_build(self) -> None:
        with mock.patch.object(t, "sandboxed", unsandboxed), mock.patch.object(t, "run_cmd", return_value=1):
            self.assertEqual(t.run_repro_in(self.d, ["attack.round1.F1"], self.d / "log", 10)[0], "no-build")

    def test_check_mutants_reports_incomplete_with_exit_5(self) -> None:
        proj = self.d / "proj"
        (proj / "src/main").mkdir(parents=True)
        (proj / "src/main/A.kt").write_text("val x = 1\n")
        muts = self.d / "m.json"
        muts.write_text(json.dumps([{"name": "m1", "file": "src/main/A.kt", "old": "1", "new": "2"}]))
        args = type("A", (), {"project": str(proj), "mutants": str(muts), "out": str(self.d / "out.json"),
                              "logs": str(self.d), "timeout": 10})()
        with mock.patch.object(t, "run_repro_in", return_value=("incomplete", [])), \
                self.assertRaises(SystemExit) as e:
            t.cmd_check_mutants(args)
        self.assertEqual(e.exception.code, 5, "incomplete, not 'no usable planted bug' (1)")


class InventoryRepros(unittest.TestCase):
    """Importing an accepted repro appends to the tests role's inventory; its completion record must stay valid for
    exactly that change and nothing else (found on Phase 5 run 2: REPORT.md said NOT DONE after F1 was imported)."""

    def setUp(self) -> None:
        self.p = Path(tempfile.mkdtemp())
        self.rundir = self.p / ".sdlc"
        (self.rundir / "attack/round-1").mkdir(parents=True)
        (self.p / "src/test").mkdir(parents=True)
        self.inv = self.p / "src/test/tests-inventory.json"
        self.inv.write_text('{"cases": [{"class": "A", "name": "a", "kind": "property", "spec": ["INV-1"]}]}')
        self.recorded = t.sha_file(self.inv)
        case = {"class": "attack.round1.F1", "name": "f1 fails"}
        (self.rundir / "attack/round-1/findings.validated.json").write_text(json.dumps({"findings": [
            {"id": "F1", "status": "reproduced", "round": 1, "spec_refs": ["INV-9"], "cases": [case]},
            {"id": "F2", "status": "discarded", "round": 1, "cases": [{"class": "attack.round1.F2", "name": "x"}]}]}))
        args = type("A", (), {"inventory": str(self.inv), "validated": str(self.rundir / "attack/round-1/findings.validated.json")})()
        t.cmd_inventory_add_repros(args)

    def test_the_tests_inventory_plus_accepted_repros_is_valid(self) -> None:
        self.assertNotEqual(t.sha_file(self.inv), self.recorded, "the import changed the file")
        self.assertTrue(t.inventory_with_repros_ok(self.rundir, self.inv, self.recorded))

    def test_an_edited_case_of_the_tests_role_is_not(self) -> None:
        d = json.loads(self.inv.read_text())
        d["cases"][0]["name"] = "renamed"
        self.inv.write_text(json.dumps(d))
        self.assertFalse(t.inventory_with_repros_ok(self.rundir, self.inv, self.recorded))

    def test_a_repro_case_without_a_reproduced_finding_is_not(self) -> None:
        d = json.loads(self.inv.read_text())
        d["cases"].append({"class": "attack.round1.F2", "name": "x", "kind": "repro", "finding": "round 1 F2"})
        self.inv.write_text(json.dumps(d))
        self.assertFalse(t.inventory_with_repros_ok(self.rundir, self.inv, self.recorded), "F2 was discarded")

    def test_a_tampered_base_copy_is_not(self) -> None:
        (self.rundir / t.INVENTORY_BASE).write_text('{"cases": []}')
        self.assertFalse(t.inventory_with_repros_ok(self.rundir, self.inv, self.recorded))


class OpenQuestions(unittest.TestCase):
    def test_sub_bullets_belong_to_their_item(self) -> None:
        md = "## Open questions\n\n- First item.\n  - sub one;\n  - sub two.\n- Second item\n  wrapped.\n## Next\n"
        self.assertEqual(t.md_section(md, "open"), ["First item. sub one; sub two.", "Second item wrapped."])


if __name__ == "__main__":
    unittest.main()
