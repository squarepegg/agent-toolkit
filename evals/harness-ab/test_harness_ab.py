#!/usr/bin/env python3
"""Tests for the harness-ab tooling: split losslessness, dst_lint planted bugs, graders satisfiable AND discriminating,
report verdict rule. Stdlib only, no model.  python3 evals/harness-ab/test_harness_ab.py"""
import json, os, shutil, subprocess, sys, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__)); ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "scripts"))
import dst_lint, report, split_skill, tasks as T  # noqa: E402

ENGINE = os.path.join(ROOT, "skills", "dst", "templates", "python", "dst.py")

GOOD_SUITE = '''import unittest
from dst import run_suite, known_bug
from cache import ReadThroughCache, Store, update


class HookStore(Store):
    def __init__(self):
        super().__init__(); self.on_inside_read = None

    def read(self, key):
        v = super().read(key)
        if self.on_inside_read: h, self.on_inside_read = self.on_inside_read, None; h()
        return v


def scenario(sim):
    store = HookStore(); cache = ReadThroughCache(store, ttl_seconds=5.0, clock=lambda: sim.clock.ticker_nanos() / 1e9)
    truth, n = {}, 0
    for i in range(40):
        op = sim.choose(["get", "update", "update_during_read", "advance"]); k = sim.choose(["a", "b"])
        sim.record_step(f"op[{i}] {op} {k}")
        if op in ("update", "update_during_read"):
            n += 1; v = f"{k}{n}"
            if op == "update": update(store, cache, k, v); truth[k] = v
            else:
                def hook(k=k, v=v): update(store, cache, k, v); truth[k] = v
                store.on_inside_read = hook; cache.get(k); store.on_inside_read = None
                sim.sometimes("update_during_read", True)
        elif op == "get":
            got = cache.get(k)
            sim.always("NoStaleReadAfterInvalidate", got == truth.get(k), lambda: f"got={got} truth={truth.get(k)}")
        else:
            sim.clock.advance_millis(sim.choose([1000, 6000]))
    return repr(sorted(truth.items()))


class CacheSim(unittest.TestCase):
    def test_known_bug_1_stale_value_after_update_during_read(self):
        def repro():
            with self.assertRaises(AssertionError):
                run_suite("cache", {"update_during_read"}, scenario, default_seed_count=60)
        known_bug("#1", "a get after update returns the new value", repro)
'''

NAIVE_SUITE = '''import unittest
from dst import run_suite
from cache import ReadThroughCache, Store, update


def scenario(sim):
    store = Store(); cache = ReadThroughCache(store, clock=lambda: sim.clock.ticker_nanos() / 1e9); truth = {}
    for i in range(40):
        k = sim.choose(["a", "b"]); sim.record_step(f"op {i}")
        if sim.chance(50): update(store, cache, k, i); truth[k] = i
        else:
            got = cache.get(k); sim.always("NoStale", got == truth.get(k)); sim.sometimes("hit", True)
    return repr(sorted(truth.items()))


class CacheSim(unittest.TestCase):
    def test_seed_sweep_cache_40_ops(self):
        run_suite("cache", {"hit"}, scenario)
'''

REPORT = ("Engine: template v1\nSUT (real classes): ReadThroughCache\nPhysical limits modelled: TTL\nInvariants (always): NoStaleReadAfterInvalidate\n"
          "Sometimes: 1 declared\nMutation checks: removed invalidate -> caught\nFindings: stale read, prod-reachable=yes\nNot covered: threads")


def repo_with(suite):
    r = tempfile.mkdtemp(); T.dst_setup(r); shutil.copy(ENGINE, os.path.join(r, "dst.py"))
    if suite: open(os.path.join(r, "tests", "test_cache_sim.py"), "w").write(suite)
    return r


class Split(unittest.TestCase):
    def test_every_skill_splits_losslessly_and_tamper_is_caught(self):
        for d in sorted(os.listdir(os.path.join(ROOT, "skills"))):
            src = os.path.join(ROOT, "skills", d); out = tempfile.mkdtemp() + "/s"
            split_skill.split(src, out, [r"^## (Hard rules|Overview)"])
            ok, msg = split_skill.check(src, out); self.assertTrue(ok, f"{d}: {msg}")
            secs = os.path.join(out, "reference", "sections")
            if os.path.isdir(secs) and os.listdir(secs):
                f = os.path.join(secs, sorted(os.listdir(secs))[0]); open(f, "a").write("x")
                self.assertFalse(split_skill.check(src, out)[0], d)

    def test_keeps_fenced_hash_lines(self):
        src = tempfile.mkdtemp(); open(src + "/SKILL.md", "w").write("---\nname: x\n---\n# T\n## Keep\n```\n## not a heading\n```\n## Move\nbody\n")
        out = src + "-o"; split_skill.split(src, out, ["^## Keep"])
        s = open(out + "/SKILL.md").read(); self.assertIn("## not a heading", s); self.assertNotIn("body", s)
        self.assertTrue(split_skill.check(src, out)[0])


class ShippedSkills(unittest.TestCase):
    def test_every_reference_pointer_resolves_also_through_a_symlink_install(self):
        import re
        link_dir = tempfile.mkdtemp()
        for d in sorted(os.listdir(os.path.join(ROOT, "skills"))):
            src = os.path.join(ROOT, "skills", d); link = os.path.join(link_dir, d); os.symlink(src, link)
            text = open(os.path.join(link, "SKILL.md")).read()
            for rel in re.findall(r"`((?:reference|templates|scripts)/[\w./-]+\.(?:md|py|sh|kt|json))`", text):
                self.assertTrue(os.path.isfile(os.path.join(link, rel)), f"{d}: pointer {rel} does not resolve")

    def test_doubt_is_the_split_layout_and_stays_small(self):
        d = os.path.join(ROOT, "skills", "doubt"); text = open(os.path.join(d, "SKILL.md")).read()
        self.assertIn(split_skill.POINTER_HEAD, text); self.assertLessEqual(len(text), 11500)
        self.assertEqual(len(os.listdir(os.path.join(d, "reference", "sections"))), 5)


class Lint(unittest.TestCase):
    def errs(self, text): return dst_lint.lint_text("t.py", text)

    def test_good_suite_is_clean(self):
        self.assertEqual(self.errs(GOOD_SUITE), [])

    def test_planted_bugs_each_caught(self):
        planted = {
            "D1": GOOD_SUITE.replace("k = sim.choose([\"a\", \"b\"])", "import random; k = random.choice([\"a\", \"b\"])"),
            "D1t": GOOD_SUITE.replace("lambda: sim.clock.ticker_nanos() / 1e9", "time.time"),
            "D2": GOOD_SUITE.replace("test_known_bug_1_stale", "test_stale"),
            "D2b": GOOD_SUITE.replace("test_known_bug_1_stale", "test_bug_catch_stale"),
            "D3": GOOD_SUITE.replace('known_bug("#1", "a get after update returns the new value", repro)', "repro()"),
            "D4": GOOD_SUITE.replace("default_seed_count=60)", "default_seed_count=60, verify_seeds=0)"),
            "D5": GOOD_SUITE.replace('sim.sometimes("update_during_read", True)', "pass"),
            "D6": NAIVE_SUITE.replace("test_seed_sweep_cache_40_ops", "test_mutant_catch_no_invalidate"),
        }
        for rule, text in planted.items():
            self.assertNotEqual(text, GOOD_SUITE, rule)
            self.assertTrue(any(e.startswith(rule[:2]) for e in self.errs(text)), f"{rule} not caught: {self.errs(text)}")

    def test_no_false_positives_seen_in_smoke(self):
        extra = (GOOD_SUITE + "\n    def test_mutant_catch_harness_random_trips_replay(self):\n        def bad(sim):\n            return str(random.random())\n"
                 "        with self.assertRaises(AssertionError):\n            run_suite('m', set(), bad)\n"
                 "\n    def test_seed_sweep_unrestricted_known_bugs_filtered(self):\n        run_suite('c', {'update_during_read'}, scenario)\n"
                 "\n    def test_accepted_ttl_staleness(self):\n        run_suite('c', {'update_during_read'}, scenario)\n"
                 "\n    def test_mutant_catch_known_bug_filter_off(self):\n        with self.assertRaises(AssertionError):\n            run_suite('c', set(), scenario)\n")
        self.assertEqual(self.errs(extra), [])
        self.assertTrue(self.errs(extra.replace("test_mutant_catch_harness_random", "test_seed_sweep_harness_random")))

    def test_non_sim_file_ignored(self):
        self.assertEqual(self.errs("import random\nrandom.random()\n"), [])


class DstGrader(unittest.TestCase):
    def test_reference_suite_passes_every_check(self):
        r = repo_with(GOOD_SUITE); c, d = T.dst_grade(r, REPORT, REPORT)
        self.assertTrue(all(c.values()), (c, d))

    def test_sequential_only_suite_does_not_detect_the_race(self):
        r = repo_with(NAIVE_SUITE); c, d = T.dst_grade(r, REPORT, REPORT)
        self.assertFalse(c["detects_planted_race"], d); self.assertTrue(c["no_false_alarm_on_fixed_cache"], d)

    def test_no_suite_and_edited_cache(self):
        r = repo_with(None); open(os.path.join(r, "cache.py"), "a").write("# x\n"); c, _ = T.dst_grade(r, "", "")
        self.assertFalse(c["sim_suite_exists"] or c["detects_planted_race"] or c["cache_py_unchanged"] or c["report_fields"])

    def test_fixed_cache_really_fixes_it(self):
        r = repo_with(GOOD_SUITE.replace("test_known_bug_1_stale_value_after_update_during_read", "test_seed_sweep_x")
                      .replace('known_bug("#1", "a get after update returns the new value", repro)', 'run_suite("cache", {"update_during_read"}, scenario)'))
        bug, = [v for k, v in T.unittest_outcomes(r).items() if "sim" in k]
        open(os.path.join(r, "cache.py"), "w").write(T.CACHE_FIXED)
        fix, = [v for k, v in T.unittest_outcomes(r).items() if "sim" in k]
        self.assertEqual((bug, fix), ("FAIL", "ok"))


class DoubtGrader(unittest.TestCase):
    def setUp(self): self.r = tempfile.mkdtemp(); T.doubt_setup(self.r)

    def test_good_run(self):
        txt = "Want a cross-model second opinion (Gemini CLI, Codex CLI, manual, skip)?\nProceeding with single-model findings only. Step 3 drops email before v2 deploys: v1 breaks and rollback is impossible."
        c, _ = T.doubt_grade(self.r, txt, txt, ["cat > /tmp/p.md <<'EOF'\nx\nEOF", "pi -p --no-session < /tmp/p.md"])
        self.assertTrue(all(c.values()), c)

    def test_bad_run(self):
        c, _ = T.doubt_grade(self.r, "Looks fine.", "Looks fine.", ['pi -p "review $(cat plan.md)"', "gemini -p x", "pi -p a", "pi -p b", "pi -p c"])
        self.assertEqual([k for k, v in c.items() if v], ["fresh_reviewer_spawned", "plan_unchanged"])

    def test_which_gemini_is_not_an_invocation(self):
        c, _ = T.doubt_grade(self.r, "", "", ["which gemini codex", "gemini --version", "cd x; cat a b; which pi gemini codex claude; env | grep ^PI_"])
        self.assertTrue(c["no_unauthorized_external_cli"])
        c, _ = T.doubt_grade(self.r, "", "", ["cd /tmp && gemini --approval-mode plan -p '' < p.md"])
        self.assertFalse(c["no_unauthorized_external_cli"])


class Report(unittest.TestCase):
    def row(self, task, arm, i, passed, tok, viol=0, checks=None):
        checks = checks or {"a": passed > 0, "lint_clean": viol == 0}
        return {"task": task, "arm": arm, "i": i, "checks": checks, "passed": sum(checks.values()), "n_checks": len(checks), "finished": True,
                "usage": {"input": tok, "cacheRead": 0, "cacheWrite": 0, "cost": 0.1}, "detail": {"rule_violations": viol}, "wall_s": 1}

    def test_verdicts(self):
        rows = [self.row("dst", "A", i, 1, 1000, 3) for i in range(3)] + [self.row("dst", "B", i, 1, 850, 3) for i in range(3)] + \
               [self.row("dst", "C", i, 1, 1200, 0) for i in range(3)]
        _, v = report.summarize(rows); self.assertTrue(v["H1 dst"][0]); self.assertTrue(v["H2 dst"][0])
        rows = [self.row("dst", "A", i, 1, 1000, 3) for i in range(3)] + [self.row("dst", "B", i, 1, 950, 3) for i in range(3)] + \
               [self.row("dst", "C", i, 0, 1200, 0) for i in range(3)]
        _, v = report.summarize(rows); self.assertFalse(v["H1 dst"][0]); self.assertFalse(v["H2 dst"][0])


if __name__ == "__main__":
    unittest.main(verbosity=1)
