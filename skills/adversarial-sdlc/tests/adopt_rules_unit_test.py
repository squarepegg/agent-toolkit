#!/usr/bin/env python3
"""adopt_rules.py (Amendment B3): adopting staged rules is one atomic batch. It refuses what could weaken the gate
(a rule that still matches the current code, a different file under an adopted name, a reused rule id, a conflicting
GATE_RULES, a path that escapes or is outside the lock), copies nothing on refusal, and leaves an audit record."""
from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_s = importlib.util.spec_from_file_location("adopt_rules", ROOT / "scripts/adopt_rules.py")
if _s is None or _s.loader is None:
    raise RuntimeError("cannot load adopt_rules.py")
ar = importlib.util.module_from_spec(_s)
_s.loader.exec_module(ar)

RULE = "id: eval-call\nlanguage: python\nseverity: error\nmessage: no eval\nrule:\n  pattern: eval($X)\n"
RULE2 = "id: exec-call\nlanguage: python\nseverity: error\nmessage: no exec\nrule:\n  pattern: exec($X)\n"


class Adopt(unittest.TestCase):
    def setUp(self) -> None:
        d = Path(tempfile.mkdtemp())
        self.p, self.rundir = d / "proj", d / "proj/.sdlc"
        (self.p / "src/main").mkdir(parents=True)
        (self.p / "src/main/m.py").write_text("def f(s):\n    return int(s)\n")
        (self.p / "gate.config").write_text('GATE_MAIN_PATHS="src/main"\n')
        self.stage(1, "eval-call.yml", RULE)
        self.stage(1, "exec-call.yml", RULE2)

    def stage(self, rnd: int, name: str, text: str) -> None:
        f = self.rundir / f"consolidate/round-{rnd}/staged-rules/{name}"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)

    def adopt(self, names: list[str] | None = None, **kw):
        return ar.adopt(self.p, self.rundir, names or [], how="relayed by agent", note="user: yes", **kw)

    def test_adopts_all_staged_rules_sets_gate_rules_and_leaves_an_audit_record(self) -> None:
        r = self.adopt()
        self.assertEqual((r["adopted"], r["refused"]), (["eval-call.yml", "exec-call.yml"], []))
        self.assertEqual(r["files"], ["sdlc-rules/eval-call.yml", "sdlc-rules/exec-call.yml", "gate.config"])
        self.assertEqual((self.p / "sdlc-rules/eval-call.yml").read_text(), RULE)
        self.assertTrue((self.p / "gate.config").read_text().endswith('GATE_RULES="sdlc-rules"\n'))
        rec = json.loads((self.rundir / "consolidate/adopted.jsonl").read_text())
        self.assertEqual((rec["how"], rec["note"], sorted(rec["rules"])), ("relayed by agent", "user: yes",
                                                                           ["eval-call.yml", "exec-call.yml"]))

    def test_named_subset_and_unknown_name(self) -> None:
        r = self.adopt(["eval-call.yml"])
        self.assertEqual(r["adopted"], ["eval-call.yml"])
        self.assertFalse((self.p / "sdlc-rules/exec-call.yml").exists())
        r = self.adopt(["nope.yml"])
        self.assertTrue(any("nope.yml: not staged" in x for x in r["refused"]))

    def test_dry_run_writes_nothing(self) -> None:
        before = (self.p / "gate.config").read_text()
        r = self.adopt(dry_run=True)
        self.assertEqual(len(r["adopted"]), 2)
        self.assertFalse((self.p / "sdlc-rules").exists())
        self.assertEqual((self.p / "gate.config").read_text(), before)
        self.assertFalse((self.rundir / "consolidate/adopted.jsonl").exists())

    def test_a_rule_matching_the_current_code_refuses_the_whole_batch(self) -> None:
        (self.p / "src/main/m.py").write_text("def f(s):\n    exec(s)\n    return int(s)\n")
        r = self.adopt()
        self.assertEqual(r["adopted"], [])
        self.assertTrue(any(x.startswith("exec-call.yml: matches the current main sources") and "m.py:2" in x
                            for x in r["refused"]), r)
        self.assertFalse((self.p / "sdlc-rules").exists())  # atomic: eval-call.yml was fine but not copied
        self.assertFalse((self.rundir / "consolidate/adopted.jsonl").exists())

    def test_rules_only_add(self) -> None:
        self.adopt()
        r = self.adopt()  # same again: nothing to do, not an error
        self.assertEqual((r["adopted"], r["already"], r["refused"]), ([], ["eval-call.yml", "exec-call.yml"], []))
        (self.p / "sdlc-rules/eval-call.yml").write_text(RULE.replace("no eval", "weaker"))
        r = self.adopt()
        self.assertTrue(any("different file of that name" in x for x in r["refused"]))

    def test_rule_ids_must_be_unique_and_present(self) -> None:
        self.stage(1, "dup.yml", RULE)  # same id as eval-call.yml, different name
        r = self.adopt()
        self.assertTrue(any(x.startswith(("eval-call.yml: id 'eval-call' already used by dup.yml",
                                          "dup.yml: id 'eval-call' already used by eval-call.yml")) for x in r["refused"]), r)
        (self.rundir / "consolidate/round-1/staged-rules/dup.yml").write_text("language: python\n")
        r = self.adopt(["dup.yml"])
        self.assertTrue(any(x.startswith("dup.yml: no id:") for x in r["refused"]))

    def test_conflicting_staged_content_in_two_rounds_is_refused(self) -> None:
        self.stage(2, "eval-call.yml", RULE.replace("no eval", "different"))
        self.assertTrue(any("staged with different content" in x for x in self.adopt()["refused"]))

    def test_gate_rules_config_cases(self) -> None:
        (self.p / "gate.config").write_text('GATE_MAIN_PATHS="src/main"\nGATE_RULES="other"\n')
        self.assertTrue(any("already sets GATE_RULES='other'" in x for x in self.adopt()["refused"]))
        (self.p / "gate.config").write_text('GATE_MAIN_PATHS="src/main"\nGATE_RULES="sdlc-rules"\n')
        r = self.adopt()
        self.assertEqual(r["refused"], [])
        self.assertNotIn("gate.config", r["files"])  # already set: not touched, not re-locked
        self.assertEqual((self.p / "gate.config").read_text().count("GATE_RULES"), 1)

    def test_unsafe_rule_directories_are_refused(self) -> None:
        for bad in (".sdlc/rules", "../rules", "/etc", ""):
            r = ar.adopt(self.p, self.rundir, [], bad)
            self.assertTrue(r["refused"] and not r["adopted"], bad)
        self.assertFalse((self.p / ".sdlc/rules").exists())

    def test_a_rules_dir_that_is_a_symlink_out_of_the_project_is_refused(self) -> None:
        outside = Path(tempfile.mkdtemp())
        (self.p / "sdlc-rules").symlink_to(outside)
        r = self.adopt()
        self.assertTrue(any("symlink or resolves outside" in m for m in r["refused"]), r)
        self.assertEqual(list(outside.iterdir()), [])
        (self.p / "sdlc-rules").unlink()
        (self.p / "real").mkdir()
        (self.p / "viaparent").symlink_to(outside)
        r = ar.adopt(self.p, self.rundir, [], "viaparent/sub")
        self.assertTrue(r["refused"] and not r["adopted"])
        self.assertEqual(list(outside.iterdir()), [])

    def test_symlinked_and_oddly_named_staged_files_are_ignored(self) -> None:
        d = self.rundir / "consolidate/round-1/staged-rules"
        outside = self.p / "outside.yml"
        outside.write_text(RULE.replace("eval-call", "evil"))
        (d / "link.yml").symlink_to(outside)
        (d / ".hidden.yml").write_text(RULE.replace("eval-call", "hid"))
        (d / "notes.txt").write_text("x")
        staged, conflicts = ar.staged_rules(self.rundir)
        self.assertEqual((sorted(staged), conflicts), (["eval-call.yml", "exec-call.yml"], []))

    def test_nothing_staged_is_a_noop_and_missing_ast_grep_is_an_error(self) -> None:
        empty = Path(tempfile.mkdtemp())
        self.assertEqual(ar.adopt(self.p, empty, []) | {"dry_run": False}, {"adopted": [], "already": [], "refused": [], "files": [], "dry_run": False})
        with mock.patch.object(ar, "_rv") as rv, self.assertRaises(RuntimeError):
            rv.return_value.find_sg.return_value = None
            ar.adopt(self.p, self.rundir, [])

    def test_cli_exit_codes(self) -> None:
        base = ["--project", str(self.p), "--run", str(self.rundir)]
        self.assertEqual(ar.main([*base, "--dry-run"]), 0)
        (self.p / "src/main/m.py").write_text("eval(1)\n")
        self.assertEqual(ar.main(base), 1)
        self.assertEqual(ar.main([*base, "--rules-dir", "../x"]), 1)

    def test_ids_are_checked_against_rules_already_adopted(self) -> None:
        self.adopt(["eval-call.yml"])
        self.stage(2, "renamed.yml", RULE)  # same id as the adopted eval-call.yml, new name
        r = self.adopt(["renamed.yml"])
        self.assertTrue(any("id 'eval-call' already used by eval-call.yml" in m for m in r["refused"]), r)

    def test_a_second_batch_into_an_existing_directory_and_a_nested_directory(self) -> None:
        self.assertEqual(self.adopt(["eval-call.yml"])["refused"], [])
        r = self.adopt(["exec-call.yml"])  # the rules dir, the audit log and its directory already exist
        self.assertEqual((r["adopted"], r["refused"]), (["exec-call.yml"], []))
        self.assertEqual(len((self.rundir / "consolidate/adopted.jsonl").read_text().splitlines()), 2)
        (self.p / "gate.config").write_text('GATE_MAIN_PATHS="src/main"\n')  # a fresh config: no GATE_RULES yet
        r = ar.adopt(self.p, self.rundir, ["eval-call.yml"], "deep/er/rules")
        self.assertEqual(r["adopted"], ["eval-call.yml"])
        self.assertTrue((self.p / "deep/er/rules/eval-call.yml").is_file())

    def test_gate_config_is_appended_to_cleanly(self) -> None:
        for before, want in (('A=1', 'A=1\nGATE_RULES="sdlc-rules"\n'), ('A=1\n', 'A=1\nGATE_RULES="sdlc-rules"\n'),
                             ('', 'GATE_RULES="sdlc-rules"\n')):
            (self.p / "gate.config").write_text(before)
            if (self.p / "sdlc-rules").exists():
                for f in (self.p / "sdlc-rules").iterdir():
                    f.unlink()
            self.adopt(["eval-call.yml"])
            self.assertEqual((self.p / "gate.config").read_text(), want)

    def test_a_missing_ast_grep_is_exit_2_on_the_command_line(self) -> None:
        with mock.patch.object(ar, "_rv") as rv:
            rv.return_value.find_sg.return_value = None
            self.assertEqual(ar.main(["--project", str(self.p), "--run", str(self.rundir)]), 2)

    def test_project_and_run_are_required_options(self) -> None:
        for argv in (["--run", str(self.rundir)], ["--project", str(self.p)]):
            with self.assertRaises(SystemExit) as cm:
                ar.main(argv)
            self.assertEqual(cm.exception.code, 2)

    def test_config_parsing(self) -> None:
        for text, want in (('GATE_RULES="a b"\n', "a b"), ("GATE_RULES='x'\n", "x"), ("GATE_RULES=y\n", "y"),
                           ("# GATE_RULES=z\n", None), ("OTHER_GATE_RULES=z\n", None), ("", None)):
            self.assertEqual(ar.configured_dir(text), want, text)
        self.assertEqual(ar._main_paths('GATE_MAIN_PATHS="a b"\n'), ["a", "b"])
        self.assertEqual(ar._main_paths(""), ["src/main"])
        self.assertEqual(ar._main_paths("GATE_MAIN_PATHS='c d'\n"), ["c", "d"])
        self.assertEqual(ar._main_paths("GATE_MAIN_PATHS=e\n"), ["e"])
        self.assertEqual(ar._main_paths('GATE_MAIN_PATHS=""\n'), ["src/main"])


if __name__ == "__main__":
    unittest.main()
