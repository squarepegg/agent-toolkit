"""Tests for templates/python/dst.py (the Python sketch of the Kotlin engine). Run: python3 skills/dst/tests/test_python_template.py"""
import importlib.util
import os
import random
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
DST_PATH = HERE.parent / "templates" / "python" / "dst.py"
LINT = HERE.parents[2] / "evals" / "harness-ab" / "dst_lint.py"

spec = importlib.util.spec_from_file_location("dst_under_test", DST_PATH)
dst = importlib.util.module_from_spec(spec)
sys.modules["dst_under_test"] = dst
spec.loader.exec_module(dst)

ENV_KEYS = ("SIM_SEED", "SIM_SEEDS", "SIM_SEED_START")


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if k not in ENV_KEYS}
    env.update(extra)
    return mock.patch.dict(os.environ, env, clear=True)


def simple(sim):
    for i in range(5):
        sim.record_step(f"s{i} {sim.choose('abc')}")
        sim.sometimes("hit", sim.chance(50) or i == 4)
    return "ok"


class Determinism(unittest.TestCase):
    def run_once(self, seed):
        ctx = dst.SimulationContext(seed, {"hit"})
        simple(ctx)
        return ctx.transcript_hash

    def test_same_seed_same_transcript(self):
        self.assertEqual(self.run_once(7), self.run_once(7))

    def test_different_seed_different_transcript(self):
        self.assertNotEqual(self.run_once(7), self.run_once(8))

    def test_fault_description_changes_hash(self):
        a, b = dst.SimulationContext(1), dst.SimulationContext(1)
        a.record_fault("x")
        b.record_fault("y")
        self.assertNotEqual(a.transcript_hash, b.transcript_hash)


class ReplayCheck(unittest.TestCase):
    def test_outside_randomness_is_caught(self):
        def scenario(sim):
            sim.record_step("a")
            return str(random.random())  # module-level random: different on replay
        with clean_env(SIM_SEEDS="1"), self.assertRaisesRegex(AssertionError, r"Non-determinism .* seed=1"):
            dst.run_suite("S", set(), scenario)

    def test_outside_randomness_in_transcript_only_is_caught(self):
        def scenario(sim):
            sim.record_step(f"v={random.random()}")
            return "same digest"
        with clean_env(SIM_SEEDS="1"), self.assertRaisesRegex(AssertionError, "Non-determinism"):
            dst.run_suite("S", set(), scenario)

    def test_leaked_state_is_caught(self):
        leaked = []
        def scenario(sim):
            leaked.append(1)
            sim.record_step(f"calls={len(leaked)}")
            return "x"
        with clean_env(SIM_SEEDS="1"), self.assertRaisesRegex(AssertionError, "Non-determinism"):
            dst.run_suite("S", set(), scenario)

    def test_deterministic_scenario_passes(self):
        with clean_env(SIM_SEEDS="3"):
            s = dst.run_suite("S", {"hit"}, simple)
        self.assertEqual(s.seeds, [1, 2, 3])

    def test_verify_seeds_limits_replay(self):
        calls = []
        def scenario(sim):
            calls.append(sim.seed)
            return "x"
        with clean_env(SIM_SEEDS="4"):
            dst.run_suite("S", set(), scenario, verify_seeds=2)
        self.assertEqual(calls, [1, 1, 2, 2, 3, 4])


class AntiVacuity(unittest.TestCase):
    def test_never_reached_sometimes_fails_sweep(self):
        def scenario(sim):
            sim.sometimes("rare", False)
            return "x"
        with clean_env(SIM_SEEDS="5"), self.assertRaisesRegex(AssertionError, r"Vacuous .*\['rare'\]"):
            dst.run_suite("S", {"rare"}, scenario)

    def test_reached_on_any_seed_is_enough(self):
        def scenario(sim):
            sim.sometimes("rare", sim.seed == 3)
            return "x"
        with clean_env(SIM_SEEDS="5"):
            self.assertIn("rare", dst.run_suite("S", {"rare"}, scenario).reached)

    def test_single_seed_skips_gate(self):
        def scenario(sim):
            sim.sometimes("rare", False)
            return "x"
        with clean_env(SIM_SEED="9"):
            s = dst.run_suite("S", {"rare"}, scenario)
        self.assertTrue(s.single_seed)

    def test_undeclared_sometimes_rejected(self):
        with self.assertRaisesRegex(ValueError, "Undeclared"):
            dst.SimulationContext(1, {"a"}).sometimes("b", True)


class FailureText(unittest.TestCase):
    def test_always_failure_has_seed_repro_trace_detail(self):
        ctx = dst.SimulationContext(42)
        ctx.record_step("put k=1")
        with self.assertRaises(AssertionError) as cm:
            ctx.always("Inv", False, lambda: "got=1 want=2")
        msg = str(cm.exception)
        for needle in ("[Inv] violated", "seed=42", "SIM_SEED=42", "Detail: got=1 want=2", "Recent trace (1 steps)", "#1 @", "put k=1"):
            self.assertIn(needle, msg)

    def test_always_true_does_not_raise_or_eval_detail(self):
        dst.SimulationContext(1).always("Inv", True, lambda: 1 / 0)

    def test_run_suite_wraps_plain_assertion_with_report(self):
        def scenario(sim):
            sim.record_step("hello")
            assert False, "boom"
        with clean_env(SIM_SEEDS="1"), self.assertRaises(AssertionError) as cm:
            dst.run_suite("Suite", set(), scenario)
        for needle in ("Suite [Suite] assertion failed: boom", "SIM_SEED=1", "hello"):
            self.assertIn(needle, str(cm.exception))

    def test_run_suite_does_not_double_wrap_always(self):
        def scenario(sim):
            sim.always("Inv", False)
        with clean_env(SIM_SEEDS="1"), self.assertRaises(AssertionError) as cm:
            dst.run_suite("Suite", set(), scenario)
        self.assertNotIn("assertion failed", str(cm.exception))
        self.assertIn("[Inv] violated", str(cm.exception))


class FaultSchedule(unittest.TestCase):
    def test_fault_survives_trace_eviction(self):
        ctx = dst.SimulationContext(5, trace_capacity=10)
        ctx.record_step("early-step")
        ctx.record_fault("early-fault")
        for i in range(50):
            ctx.record_step(f"late{i}")
        with self.assertRaises(AssertionError) as cm:
            ctx.always("Inv", False)
        msg = str(cm.exception)
        self.assertNotIn("early-step", msg)
        self.assertIn("Recent trace (10 steps)", msg)
        self.assertIn("Fault schedule (1 faults, oldest first):", msg)
        self.assertIn("early-fault", msg)
        self.assertEqual(len(ctx.fault_schedule_snapshot()), 1)
        self.assertIn("#1 @", ctx.fault_schedule_snapshot()[0])

    def test_no_section_without_faults(self):
        self.assertNotIn("Fault schedule", dst.SimulationContext(1).failure_report("h"))

    def test_cap_keeps_newest_oldest_first(self):
        ctx = dst.SimulationContext(1)
        n = dst.MAX_FAULTS_KEPT
        self.assertEqual(n, 1000)
        for i in range(n + 5):
            ctx.record_fault(f"f{i}")
        snap = ctx.fault_schedule_snapshot()
        self.assertEqual(len(snap), n)
        self.assertTrue(snap[0].endswith("f5"))
        self.assertTrue(snap[-1].endswith(f"f{n + 4}"))
        self.assertIn(f"Fault schedule ({n} faults, oldest first):", ctx.failure_report("h"))

    def test_snapshot_is_a_copy(self):
        ctx = dst.SimulationContext(1)
        ctx.record_fault("a")
        ctx.fault_schedule_snapshot().clear()
        self.assertEqual(len(ctx.fault_schedule_snapshot()), 1)


class KnownBug(unittest.TestCase):
    def test_pins_while_reproducing(self):
        dst.known_bug("#12", "adds correctly", lambda: None)  # reproduces() passing == the wrong behaviour is still there

    def test_fails_loudly_when_fixed(self):
        def reproduces():
            assert False, "wrong value gone"
        with self.assertRaisesRegex(AssertionError, r"#12 no longer reproduces.*adds correctly.*wrong value gone"):
            dst.known_bug("#12", "adds correctly", reproduces)

    def test_requires_tracker_reference(self):
        with self.assertRaises(AssertionError):
            dst.known_bug("no-ref", "x", lambda: None)


class EnvHandling(unittest.TestCase):
    def seeds(self, corpus=(), **env):
        with clean_env(**env):
            return dst._seeds(150, corpus)

    def test_default(self):
        s, single = self.seeds()
        self.assertEqual((s[0], s[-1], len(s), single), (1, 150, 150, False))

    def test_sim_seed_single(self):
        self.assertEqual(self.seeds(SIM_SEED="77", SIM_SEEDS="5"), ([77], True))

    def test_blank_sim_seed_ignored(self):
        self.assertEqual(self.seeds(SIM_SEED="  ", SIM_SEEDS="2"), ([1, 2], False))

    def test_negative_sim_seed(self):
        self.assertEqual(self.seeds(SIM_SEED="-3"), ([-3], True))

    def test_count_and_start(self):
        self.assertEqual(self.seeds(SIM_SEEDS="3", SIM_SEED_START="1001"), ([1001, 1002, 1003], False))

    def test_corpus_first_and_deduped(self):
        self.assertEqual(self.seeds(corpus=[2, 99], SIM_SEEDS="3")[0], [2, 99, 1, 3])

    def test_cap(self):
        with clean_env(SIM_SEEDS="10001"), self.assertRaises(AssertionError):
            dst._seeds(150, ())
        with clean_env(SIM_SEEDS="0"), self.assertRaises(AssertionError):
            dst._seeds(150, ())
        self.assertEqual(len(self.seeds(SIM_SEEDS="10000")[0]), 10000)


class Lint(unittest.TestCase):
    def test_shipped_python_templates_have_no_lint_errors(self):
        files = sorted(str(p) for p in DST_PATH.parent.glob("*.py"))
        self.assertIn(str(DST_PATH), files)
        r = subprocess.run([sys.executable, str(LINT), *files], capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertNotIn("ERROR", r.stdout)


if __name__ == "__main__":
    unittest.main()
