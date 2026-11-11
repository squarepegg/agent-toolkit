#!/usr/bin/env python3
"""Unit tests for sharded mutation (speed spec amendment A2): planning, shell quoting and the merge's partition
checks. Run by selftest.sh (quick). No build, no cluster."""
from __future__ import annotations

import importlib.util
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("sdlc_tool", ROOT / "scripts/sdlc_tool.py")
assert _spec is not None and _spec.loader is not None
t = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(t)

TEMPLATE = ("-Psdlc.pitestTargets={targets} -Psdlc.pitestExcludedClasses={excluded} "
            "-Psdlc.pitestFailWhenNoMutations={fail_when_empty}")


def mutant(cls: str, method: str, line: int, mutator: str = "M", status: str = "KILLED") -> str:
    return (f'<mutation detected="{"true" if status in ("KILLED", "TIMED_OUT") else "false"}" status="{status}">'
            f"<sourceFile>X.kt</sourceFile><mutatedClass>{cls}</mutatedClass><mutatedMethod>{method}</mutatedMethod>"
            f"<methodDescription>()V</methodDescription><lineNumber>{line}</lineNumber><mutator>{mutator}</mutator>"
            "<indexes><index>1</index></indexes><blocks><block>0</block></blocks></mutation>")


def report(path: Path, mutants: list[str]) -> Path:
    path.write_text('<?xml version="1.0" encoding="UTF-8"?><mutations>' + "".join(mutants) + "</mutations>")
    return path


class Plan(unittest.TestCase):
    def setUp(self) -> None:
        self.d = Path(tempfile.mkdtemp())
        ms = []
        for cls, n in (("p.A", 60), ("p.A$Inner", 40), ("p.B", 80), ("p.C", 30), ("p.D", 20), ("p.E", 10)):
            ms += [mutant(cls, "f", i) for i in range(n)]
        self.prev = report(self.d / "prev.xml", ms)

    def test_without_a_previous_report_one_unsharded_run(self) -> None:
        s = t.plan_shards(None, 8, 50)
        self.assertEqual(len(s), 1)
        self.assertIsNone(s[0]["classes"])
        self.assertIn("FailWhenNoMutations=true", t.shard_args(s, TEMPLATE, "p.*")[0], "unsharded keeps Pitest's check")

    def test_nested_classes_go_with_their_top_level_class_and_shards_are_balanced(self) -> None:
        s = t.plan_shards(self.prev, 3, 50)
        self.assertEqual(len(s), 3)
        rem = [x for x in s if x["remainder"]]
        self.assertEqual(len(rem), 1, "exactly one shard is also the remainder")
        self.assertEqual(rem[0]["weight"], min(x["weight"] for x in s), "the lightest one")
        self.assertTrue(rem[0]["classes"], "the remainder has classes of its own, so it always has mutants to report")
        explicit = s
        flat = [c for x in explicit for c in x["classes"]]
        self.assertEqual(sorted(flat), ["p.A", "p.B", "p.C", "p.D", "p.E"], "every class exactly once; A$Inner is in A")
        # A (with A$Inner) has 100 mutants and cannot be split: the best possible largest shard is
        # max(largest class, ceil(total / shards)) = max(100, 80).
        self.assertEqual(max(x["weight"] for x in explicit), 100)

    def test_shard_count_follows_the_work(self) -> None:
        self.assertEqual(len(t.plan_shards(self.prev, 16, 1000)), 1, "little work: no sharding")
        self.assertEqual(len(t.plan_shards(self.prev, 16, 60)), 4)  # ceil(240/60)
        self.assertLessEqual(len(t.plan_shards(self.prev, 3, 1)), 3, "never more than the maximum")

    def test_arguments_survive_the_shell(self) -> None:
        s = t.plan_shards(self.prev, 4, 50)
        for x, args in zip(s, t.shard_args(s, TEMPLATE, "p.*"), strict=True):
            out = subprocess.run(["bash", "-c", f"printf '%s\\n' {args}"], capture_output=True, text=True, check=True).stdout
            got = dict(line.split("=", 1) for line in out.split() if line.startswith("-P"))
            if not x["remainder"]:
                self.assertEqual(got["-Psdlc.pitestTargets"].split(","), [g for c in x["classes"] for g in (c, c + "$*")])
                self.assertEqual(got["-Psdlc.pitestExcludedClasses"], "")
            else:
                self.assertEqual(got["-Psdlc.pitestTargets"], "p.*")
                others = [c for y in s if y is not x for c in y["classes"]]
                self.assertEqual(sorted(got["-Psdlc.pitestExcludedClasses"].split(",")), sorted(g for c in others for g in (c, c + "$*")))
                self.assertFalse(set(x["classes"]) & set(others))
            self.assertEqual(got["-Psdlc.pitestFailWhenNoMutations"], "false")


class Sizing(unittest.TestCase):
    """Cost-balanced, CPU-sized shards from the previous sharded gate's timings (speed spec A2/A3)."""

    def setUp(self) -> None:
        self.d = Path(tempfile.mkdtemp())
        ms = []
        for cls, n in (("p.Heavy", 100), ("p.Mid", 100), ("p.Light", 100), ("p.Tiny", 20)):
            ms += [mutant(cls, "f", i) for i in range(n)]
        self.prev = report(self.d / "prev.xml", ms)
        self.plan = self.d / "plan.json"
        self.plan.write_text(__import__("json").dumps([
            {"id": 0, "classes": ["p.Heavy"]}, {"id": 1, "classes": ["p.Mid"]},
            {"id": 2, "classes": ["p.Light", "p.Tiny"], "remainder": True}]))
        self.result = self.d / "result.json"
        self.result.write_text(__import__("json").dumps([
            {"id": 0, "exit": 0, "threads": 5, "coverage_seconds": 30, "pitest_seconds": 630},   # 3000 thread-s
            {"id": 1, "exit": 0, "threads": 5, "coverage_seconds": 30, "pitest_seconds": 150},   # 600
            {"id": 2, "exit": 0, "threads": 5, "coverage_seconds": 30, "pitest_seconds": 90}]))  # 300

    def test_costs_come_from_measured_time_times_threads(self) -> None:
        c = t.class_costs(self.prev, self.plan, self.result)
        self.assertAlmostEqual(c["p.Heavy"], 3000)
        self.assertAlmostEqual(c["p.Mid"], 600)
        self.assertAlmostEqual(c["p.Light"] + c["p.Tiny"], 300)

    def test_a_failed_shard_teaches_nothing(self) -> None:
        r = __import__("json").loads(self.result.read_text())
        r[0]["exit"] = 97
        self.result.write_text(__import__("json").dumps(r))
        self.assertNotIn("p.Heavy", t.class_costs(self.prev, self.plan, self.result))

    def test_heavy_shards_get_more_threads_within_the_budget(self) -> None:
        costs = t.class_costs(self.prev, self.plan, self.result)
        s = t.plan_shards(self.prev, 3, 10, costs, unit_size=10, max_threads=15, cpu_budget=24)
        by = {c: x for x in s for c in x["classes"]}
        self.assertGreater(by["p.Heavy"]["threads"], by["p.Mid"]["threads"])
        self.assertLessEqual(sum(x["cpu"] for x in s), 24)
        self.assertEqual(sorted(c for x in s for c in x["classes"]), ["p.Heavy", "p.Light", "p.Mid", "p.Tiny"],
                         "sizing never changes the partition")
        self.assertEqual(sum(1 for x in s if x["remainder"]), 1)

    def test_threads_never_exceed_the_shards_analysis_units(self) -> None:
        costs = t.class_costs(self.prev, self.plan, self.result)
        s = t.plan_shards(self.prev, 3, 10, costs, unit_size=50, max_threads=15, cpu_budget=200)
        heavy = next(x for x in s if "p.Heavy" in x["classes"])
        self.assertLessEqual(heavy["threads"], 2, "100 mutants in units of 50: 2 units, so at most 2 threads")

    def test_without_timings_it_balances_by_count_and_does_not_size(self) -> None:
        s = t.plan_shards(self.prev, 3, 10, {}, cpu_budget=24)
        self.assertTrue(all("cpu" not in x for x in s))


class Untrusted(unittest.TestCase):
    def test_names_in_the_previous_report_are_validated(self) -> None:
        d = Path(tempfile.mkdtemp())
        prev = report(d / "p.xml", [mutant("p.A", "f", i) for i in range(50)] + [mutant("p.*", "f", i) for i in range(50)]
                      + [mutant("p.B'; rm -rf /", "f", i) for i in range(50)] + [mutant("p.C", "f", i) for i in range(50)])
        s = t.plan_shards(prev, 8, 10)
        self.assertEqual(sorted(c for x in s for c in x["classes"]), ["p.A", "p.C"], "globs and junk are not planned")


class Merge(unittest.TestCase):
    def setUp(self) -> None:
        self.d = Path(tempfile.mkdtemp())
        self.shards = [{"id": 0, "classes": ["p.A"]}, {"id": 1, "classes": ["p.B"]},
                       {"id": 2, "classes": ["p.C"], "remainder": True}]
        self.r = [report(self.d / "0.xml", [mutant("p.A", "f", 1), mutant("p.A$In", "g", 2, status="SURVIVED")]),
                  report(self.d / "1.xml", [mutant("p.B", "f", 1)]),
                  report(self.d / "2.xml", [mutant("p.New", "f", 9)])]

    def keys(self, p: Path) -> list[tuple]:
        return sorted(t.mutant_key(m) for m in t.parse_report_xml(p).iter("mutation"))

    def test_an_exact_partition_merges_to_the_union(self) -> None:
        out = self.d / "merged.xml"
        r = t.merge_mutations(self.shards, self.r, out)
        self.assertEqual(r["mutants"], 4)
        self.assertEqual(self.keys(out), sorted(k for p in self.r for k in self.keys(p)))
        self.assertEqual(t.parse_pitest(out)["score_exact"], "3/4", "the gate's score rules apply to the merged report")

    def test_a_split_report_merges_back_to_the_original(self) -> None:
        ms = [mutant(c, "f", i, status=("KILLED", "SURVIVED")[i % 2]) for c in ("p.A", "p.A$X", "p.B", "p.C") for i in range(7)]
        orig = report(self.d / "orig.xml", ms)
        shards = t.plan_shards(orig, 3, 5)
        parts = []
        for s in shards:
            others = [c for x in shards if x is not s for c in (x["classes"] or [])]
            cls = lambda m: t.top_class(m.split("<mutatedClass>")[1].split("<")[0])  # noqa: E731
            keep = [m for m in ms if cls(m) in s["classes"] or (s["remainder"] and cls(m) not in others)]
            parts.append(report(self.d / f"part{s['id']}.xml", keep))
        out = self.d / "merged.xml"
        t.merge_mutations(shards, parts, out)
        self.assertEqual(self.keys(out), self.keys(orig))
        self.assertEqual(t.parse_pitest(out)["score_exact"], t.parse_pitest(orig)["score_exact"])

    def test_a_missing_report_fails(self) -> None:
        with self.assertRaises(ValueError):
            t.merge_mutations(self.shards, [self.r[0], None, self.r[2]], self.d / "m.xml")
        with self.assertRaises(ValueError):
            t.merge_mutations(self.shards, self.r[:2], self.d / "m.xml")

    def test_a_foreign_mutant_fails(self) -> None:
        report(self.r[1], [mutant("p.B", "f", 1), mutant("p.A", "zz", 5)])
        with self.assertRaisesRegex(ValueError, "not in this shard"):
            t.merge_mutations(self.shards, self.r, self.d / "m.xml")

    def test_the_remainder_may_not_cover_an_assigned_class(self) -> None:
        report(self.r[2], [mutant("p.B$Inner", "f", 3)])
        with self.assertRaisesRegex(ValueError, "belongs to shard 1"):
            t.merge_mutations(self.shards, self.r, self.d / "m.xml")

    def test_mutants_told_apart_only_by_their_instruction_index_are_distinct(self) -> None:
        a = mutant("p.A", "f", 1).replace("<index>1</index>", "<index>7</index><index>9</index>")
        b = mutant("p.A", "f", 1).replace("<index>1</index>", "<index>8</index>")
        report(self.r[0], [a, b])
        self.assertEqual(t.merge_mutations(self.shards, self.r, self.d / "m.xml")["mutants"], 4)

    def test_a_duplicate_mutant_fails(self) -> None:
        report(self.r[0], [mutant("p.A", "f", 1), mutant("p.A", "f", 1)])
        with self.assertRaisesRegex(ValueError, "also in shard"):
            t.merge_mutations(self.shards, self.r, self.d / "m.xml")

    def test_a_hostile_report_is_refused(self) -> None:
        self.r[1].write_bytes(b'<!DOCTYPE x [<!ENTITY a "b">]><mutations>&a;</mutations>')
        with self.assertRaises(ValueError):
            t.merge_mutations(self.shards, self.r, self.d / "m.xml")


class ClassCosts(unittest.TestCase):
    """class_costs arithmetic and its guards, exactly."""

    def setUp(self) -> None:
        self.d = Path(tempfile.mkdtemp())

    def costs(self, mutants: list[str], shards: list[dict], results: list[dict]) -> dict:
        import json
        prev = report(self.d / "prev.xml", mutants)
        (self.d / "plan.json").write_text(json.dumps(shards))
        (self.d / "res.json").write_text(json.dumps(results))
        return t.class_costs(prev, self.d / "plan.json", self.d / "res.json")

    @staticmethod
    def with_tests(m: str, n: int) -> str:
        return m.replace('status="KILLED">', f'status="KILLED" numberOfTestsRun="{n}">')

    def test_work_is_shared_out_by_with_tests(self) -> None:
        ms = [self.with_tests(mutant("p.A", "f", 1), 9), self.with_tests(mutant("p.B", "f", 1), 1)]
        c = self.costs(ms, [{"id": 0, "classes": ["p.A", "p.B"]}],
                       [{"id": 0, "exit": 0, "threads": 2, "coverage_seconds": 10, "pitest_seconds": 110}])
        self.assertEqual(c, {"p.A": 180.0, "p.B": 20.0})  # (110-10)*2 = 200 thread-seconds, split 9:1

    def test_missing_or_zero_tests_run_counts_as_one_and_inner_classes_fold_into_their_class(self) -> None:
        ms = [mutant("p.A", "f", 1), self.with_tests(mutant("p.A$In", "g", 2), 0), mutant("p.B", "f", 1)]
        c = self.costs(ms, [{"id": 0, "classes": ["p.A", "p.B"]}],
                       [{"id": 0, "exit": 0, "threads": 1, "coverage_seconds": 0, "pitest_seconds": 30}])
        self.assertEqual(c, {"p.A": 20.0, "p.B": 10.0})  # A: 1 + max(1, 0) = 2 tests; B: 1

    def test_defaults_and_floor(self) -> None:
        one = [mutant("p.A", "f", 1)]
        shard = [{"id": 0, "classes": ["p.A"]}]
        # no threads, no coverage time: 1 thread, 0 seconds of coverage
        self.assertEqual(self.costs(one, shard, [{"id": 0, "exit": 0, "pitest_seconds": 40}]), {"p.A": 40.0})
        # threads 0 counts as 1
        self.assertEqual(self.costs(one, shard, [{"id": 0, "exit": 0, "threads": 0, "pitest_seconds": 40}]), {"p.A": 40.0})
        # coverage longer than the run: the work is floored at one thread-second
        self.assertEqual(self.costs(one, shard, [{"id": 0, "exit": 0, "coverage_seconds": 5, "pitest_seconds": 1}]),
                         {"p.A": 1.0})

    def test_shards_that_teach_nothing_are_skipped(self) -> None:
        one = [mutant("p.A", "f", 1)]
        ok = {"id": 0, "exit": 0, "threads": 1, "pitest_seconds": 10}
        shard = {"id": 0, "classes": ["p.A"]}
        self.assertEqual(self.costs(one, [shard], [ok]), {"p.A": 10.0})
        self.assertEqual(self.costs(one, [shard], [{**ok, "exit": 1}]), {})
        self.assertEqual(self.costs(one, [shard], [{k: v for k, v in ok.items() if k != "exit"}]), {})  # no exit recorded
        self.assertEqual(self.costs(one, [shard], [{**ok, "pitest_seconds": 0}]), {})
        self.assertEqual(self.costs(one, [shard], [{**ok, "id": 7}]), {})  # no result for this shard
        self.assertEqual(self.costs(one, [{"id": 0, "classes": []}], [ok]), {})
        self.assertEqual(self.costs(one, [{"id": 0, "classes": ["p.Gone"]}], [ok]), {})  # class not in the report

    def test_absent_inputs_and_invalid_class_names(self) -> None:
        prev = report(self.d / "prev.xml", [mutant("p.A", "f", 1)])
        (self.d / "plan.json").write_text("[]")
        (self.d / "res.json").write_text("[]")
        for plan, res in ((None, self.d / "res.json"), (self.d / "plan.json", None),
                          (self.d / "nope.json", self.d / "res.json"), (self.d / "plan.json", self.d / "nope.json")):
            self.assertEqual(t.class_costs(prev, plan, res), {})
        c = self.costs([mutant("bad name", "f", 1), mutant("p.A", "f", 1)], [{"id": 0, "classes": ["bad name", "p.A"]}],
                       [{"id": 0, "exit": 0, "threads": 1, "pitest_seconds": 10}])
        self.assertEqual(c, {"p.A": 10.0})


if __name__ == "__main__":
    unittest.main()
