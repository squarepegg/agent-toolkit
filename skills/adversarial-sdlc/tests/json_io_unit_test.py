#!/usr/bin/env python3
"""sdlc_tool file/JSON I/O fails closed with a message naming the file: a malformed JSON or JSON-lines input is a
ValueError (so existing handlers still catch it), a lock change that cannot be recorded in lock-history.jsonl is an
error, stale test results that cannot be removed are an error, and a bad previous shard plan only costs the schedule
its hints."""
from __future__ import annotations

import argparse
import importlib.util
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sdlc_tool", ROOT / "scripts/sdlc_tool.py")
assert _spec is not None and _spec.loader is not None
t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t)


class JsonIo(unittest.TestCase):
    def setUp(self) -> None:
        self.d = Path(tempfile.mkdtemp())

    def test_parse_json_names_the_source(self) -> None:
        self.assertEqual(t.parse_json('{"a": 1}', "x"), {"a": 1})
        with self.assertRaises(ValueError) as cm:
            t.parse_json("{nope", "plan.json")
        self.assertIn("plan.json", str(cm.exception))

    def test_load_malformed_and_missing(self) -> None:
        f = self.d / "a.json"
        f.write_text("[1,")
        with self.assertRaises(ValueError) as cm:
            t.load(f)
        self.assertIn(str(f), str(cm.exception))
        with self.assertRaises(OSError):
            t.load(self.d / "missing.json")

    def test_read_jsonl(self) -> None:
        f = self.d / "r.jsonl"
        self.assertEqual(t.read_jsonl(f), [])
        f.write_text('{"a": 1}\n\n{"a": 2}\n')
        self.assertEqual(t.read_jsonl(f), [{"a": 1}, {"a": 2}])
        f.write_text('{"a": 1}\n{bad\n')
        with self.assertRaises(ValueError) as cm:
            t.read_jsonl(f)
        self.assertIn(":2", str(cm.exception))

    def test_lock_history_reads_through_read_jsonl(self) -> None:
        (self.d / "lock-history.jsonl").write_text('{"reason": "lock"}\n')
        self.assertEqual(t.lock_history(self.d), [{"reason": "lock"}])

    def test_lock_that_cannot_be_recorded_fails(self) -> None:
        root, lock = self.d / "proj", self.d / "run" / "tests.lock"
        root.mkdir()
        lock.parent.mkdir()
        (lock.parent / "lock-history.jsonl").mkdir()  # appending to a directory fails
        a = argparse.Namespace(root=str(root), lock=str(lock), add=[], reason="", spec_sha="")
        with self.assertRaises(OSError) as cm:
            t.cmd_lock(a)
        self.assertIn("lock-history.jsonl", str(cm.exception))

    def test_class_costs_degrades_on_a_bad_plan(self) -> None:
        plan, res, prev = self.d / "plan.json", self.d / "res.json", self.d / "prev.xml"
        plan.write_text("{corrupt")
        res.write_text("[]")
        prev.write_text("")
        self.assertEqual(t.class_costs(prev, plan, res), {})
        plan.write_text("[]")
        res.write_text('[{"no_id": 1}]')  # KeyError on r["id"]
        self.assertEqual(t.class_costs(prev, plan, res), {})

    def test_stale_test_results_that_cannot_be_removed_fail(self) -> None:
        copy = self.d / "copy"
        (copy / "build/test-results/test").mkdir(parents=True)
        with mock.patch.object(t.shutil, "rmtree", side_effect=PermissionError("denied")), \
                self.assertRaises(OSError) as cm:
            t.run_repro_in(copy, ["A"], self.d / "log", 5)
        self.assertIn("stale test results", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
