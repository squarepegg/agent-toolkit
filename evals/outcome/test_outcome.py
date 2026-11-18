#!/usr/bin/env python3
"""Free tests for the outcome tier (CI): graders pinned both ways, verdict math, ledger tamper/stale handling.

  python3 evals/outcome/test_outcome.py
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import registry as R  # noqa: E402
import verdict as V  # noqa: E402

GRADED_HERE = ["property-tests", "correctness-first", "spec-first"]   # dst and doubt graders: evals/harness-ab/test_harness_ab.py


def _graded(task_id, which):
    t = R.TASKS[task_id]
    d = tempfile.mkdtemp(prefix="grader-")
    try:
        r = os.path.join(d, "repo")
        os.makedirs(r)
        t["setup"](r)
        if which != "untouched":
            t["write_solution"](r, which)
        return t["grade"](r, "", "", [])[0]
    finally:
        shutil.rmtree(d, ignore_errors=True)


class Graders(unittest.TestCase):
    def test_reference_solution_passes_every_primary_check(self):
        for tid in GRADED_HERE:
            checks = _graded(tid, "reference")
            failed = [c for c in R.TASKS[tid]["prereg"]["primary"] if not checks[c]]
            self.assertEqual(failed, [], f"{tid}: a correct solution must pass: {failed}")

    def test_naive_solution_fails_a_primary_check(self):
        for tid in GRADED_HERE:
            checks = _graded(tid, "naive")
            self.assertFalse(V.passed({"finished": True, "checks": checks}, R.TASKS[tid]["prereg"]["primary"]),
                             f"{tid}: a plausible weak solution must not pass: {checks}")

    def test_untouched_repo_fails(self):
        for tid in GRADED_HERE:
            checks = _graded(tid, "untouched")
            self.assertFalse(V.passed({"finished": True, "checks": checks}, R.TASKS[tid]["prereg"]["primary"]), tid)

    def test_existing_tests_pass_on_every_mutant_property_tests(self):
        # the mutants are plausible: the original example tests cannot tell them from the real code
        checks = _graded("property-tests", "untouched")
        self.assertTrue(checks["passes_real"])
        self.assertEqual([k for k, v in checks.items() if k.startswith("kills_") and v], [])

    def test_every_mutant_is_a_real_bug(self):
        """no equivalent mutants: each differs from the real code, on some input the real code round-trips"""
        import itertools
        import task_property_tests as P
        ns = {}
        for name, src in {"real": P.REAL, **P.MUTANTS}.items():
            ns[name] = {}
            exec(compile(src, name, "exec"), ns[name])
        strings = ["".join(c) for n in range(4) for c in itertools.product("a ,\"", repeat=n)]
        cases = [list(c) for n in range(3) for c in itertools.product(strings, repeat=n)] + [["x"] * 33, ["x"] * 40]
        real = ns["real"]
        for c in cases:
            self.assertEqual(real["parse_row"](real["format_row"](c)), c, c)           # the real code round-trips
        for name in P.MUT_NAMES:
            m = ns[name]
            bad = []
            for c in cases:
                try:
                    if m["parse_row"](m["format_row"](c)) != c:
                        bad.append(c)
                except Exception:
                    bad.append(c)
            self.assertTrue(bad, f"mutant {name} is equivalent to the real code on {len(cases)} cases")

    def test_correctness_first_scores(self):
        """score task: the reference kills every mutant, naive additions kill few, the untouched tests kill none"""
        import task_correctness_first as C
        cfg = C.PREREG["score"]

        def score(which):
            c = _graded("correctness-first", which)
            return V.run_score({"finished": True, "checks": c}, cfg)
        self.assertEqual(score("reference"), len(C.MUT_NAMES))
        self.assertEqual(score("untouched"), 0)
        self.assertEqual(score("naive"), 1)

    def test_spec_first_scores(self):
        """decision score: the reference honours all six decisions, the naive snooze only the common default, the untouched repo none"""
        import task_spec_first as S
        cfg = S.PREREG["score"]

        def score(which):
            return V.run_score({"finished": True, "checks": _graded("spec-first", which)}, cfg)
        self.assertEqual(score("reference"), len(S.DECISIONS))
        self.assertEqual(score("untouched"), 0)
        self.assertEqual(score("naive"), 1)
        self.assertEqual({k: v for k, v in _graded("spec-first", "naive").items() if k in S.DECISIONS}, {**dict.fromkeys(S.DECISIONS, False), "r1_from_now": True})

    def test_spec_first_each_decision_is_needed(self):
        """dropping any one behaviour from the reference loses exactly that decision (no check is vacuous or shared)"""
        import task_spec_first as S
        cuts = {"r1_from_now": ("now + timedelta(minutes=min(minutes, MAX_MINUTES))", "reminder.due + timedelta(minutes=min(minutes, MAX_MINUTES))"),
                "r2_limit": ("    if reminder.snoozes >= MAX_SNOOZES:\n        raise SnoozeLimit(f\"reminder {reminder.id} was already snoozed {MAX_SNOOZES} times\")\n", ""),
                "r3_persist": ('"done": self.done, "snoozes": self.snoozes}', '"done": self.done}'),
                "r4_done": ("    if reminder.done:\n        return False\n", ""),
                "r5_bounds": ("min(minutes, MAX_MINUTES)", "minutes"),
                "r6_returns": ("    return reminder.due\n", "")}
        for name, (old, new) in cuts.items():
            self.assertEqual(S.REFERENCE.count(old), 1, name)
            d = tempfile.mkdtemp(prefix="sf-cut-")
            try:
                r = os.path.join(d, "repo"); os.makedirs(r); S.setup(r); S.write_solution(r, "reference")
                with open(os.path.join(r, "reminders.py"), "w") as f:
                    f.write(S.REFERENCE.replace(old, new))
                got = S._behaviours(r)
            finally:
                shutil.rmtree(d, ignore_errors=True)
            self.assertEqual([k for k, v in got.items() if not v], [name], name)

    def test_spec_first_edited_existing_tests_or_red_suite_scores_zero(self):
        import task_spec_first as S
        for what in ("edit", "red"):
            d = tempfile.mkdtemp(prefix="sf-gate-")
            try:
                r = os.path.join(d, "repo"); os.makedirs(r); S.setup(r); S.write_solution(r, "reference")
                if what == "edit":
                    with open(os.path.join(r, "tests", "test_reminders.py"), "a") as f:
                        f.write("\n# edited\n")
                else:
                    with open(os.path.join(r, "tests", "test_red.py"), "w") as f:
                        f.write("import unittest\n\n\nclass Red(unittest.TestCase):\n    def test_red(self):\n        self.fail('x')\n")
                c = S.grade(r, "", "", [])[0]
            finally:
                shutil.rmtree(d, ignore_errors=True)
            self.assertEqual(V.run_score({"finished": True, "checks": c}, S.PREREG["score"]), 0, what)

    def test_spec_first_scripted_user_answers_only_what_is_asked(self):
        import task_spec_first as S
        a = S.answers("How many times may a reminder be snoozed?")
        self.assertEqual(len(a), 1); self.assertIn("SnoozeLimit", a[0])
        a = S.answers("Should snoozed state survive a restart? And is there a maximum number of minutes?")
        self.assertEqual(sorted(x.split()[0] for x in a), ["The", "`minutes`"])
        self.assertEqual(S.answers("Anything else I should know?"), [])
        self.assertEqual(S.answers("I will modify `snooze(reminder, minutes, now)` in place and return the new datetime. Is that fine?"), [], "restating the task is not asking")
        self.assertEqual(S.answers("Should `snooze(reminder, minutes, now)` modify the reminder in place?"), [])
        self.assertEqual(len(S.answers("Is there a maximum value for minutes?")), 1)
        self.assertIn("build it now", S.user(3, "I wrote the spec and accepted it: specs/snooze.md."), "a spec-only stop is nudged to build")
        self.assertIsNone(S.user(3, "Built it and all 5 tests pass. The spec is in specs/snooze.md."), "finished: nothing to nudge")
        self.assertIsNone(S.user(3, "Done."))
        self.assertEqual(S.answers("```\nsnooze persist limit\n```"), [], "code blocks are not questions")
        self.assertIsNone(S.user(1, "Done. Snooze is built and tested."), "no question: the scripted user ends the session")
        self.assertIsNone(S.user(S.MAX_TURNS, "What is the limit?"), "turn cap")
        self.assertIn("Accepted", S.user(2, "Here is the spec. Do you accept this spec?"))
        r = S.user(1, "Which colour should the CLI use?")
        self.assertIn("What else is still open", r); self.assertNotIn("without asking", r)   # an unmatched question must not end the interview
        # a decision the agent never asks about is never revealed: no free credit for a lucky guess at an atypical choice
        for q in ("What should snooze do for a typical user?", "Shall I write the tests first?"):
            self.assertEqual(S.answers(q), [], q)

    def test_spec_first_prompts(self):
        import task_spec_first as S
        self.assertNotIn("skill", S.PROMPT_NONE.lower())
        self.assertTrue(S.PROMPT.startswith("Use the spec-first skill.") and S.PROMPT.endswith(S.PROMPT_NONE))

    def test_correctness_first_mutants_are_real(self):
        """each mutant behaves differently from the real store on some operation sequence (no equivalent mutants)"""
        import random
        import task_correctness_first as C
        mods = {}
        for name, src in {"real": C.REAL, **C.MUTANTS}.items():
            mods[name] = {}
            exec(compile(src, name, "exec"), mods[name])

        def trace(cls, seed):
            rng, s, out = random.Random(seed), cls(), []
            for i in range(1500):
                op = rng.choice(["put", "put", "delete", "restart", "many"])
                k = rng.choice(["h0", "h1"]) if rng.random() < 0.3 else f"k{rng.randrange(150)}"
                if op == "put":
                    s.put(k, rng.choice([0, "", i]))
                elif op == "delete":
                    s.delete(k)
                elif op == "restart":
                    s.restart()
                else:
                    try:
                        s.put_many([(k, 1), (k + "x", None)])
                    except TypeError:
                        pass
                out.append(s.get(k))
            s.restart()
            return out + [s.get(f"k{j}") for j in range(150)]
        real = [trace(mods["real"]["Store"], seed) for seed in range(15)]
        for name in C.MUT_NAMES:
            self.assertNotEqual(real, [trace(mods[name]["Store"], seed) for seed in range(15)], f"{name} is equivalent")

    def test_doubt_grader_is_pinned_both_ways(self):
        import task_doubt as D
        cfg = D.PREREG["score"]
        def score(text, plan_ok=True):
            c = {"plan_unchanged": plan_ok, **{f"finds_{k}": v for k, v in D.found(text).items()}}
            return V.run_score({"finished": True, "checks": c}, cfg)
        self.assertEqual(score(D.FULL_ANSWER), len(D.FLAW_NAMES))
        self.assertEqual(score(D.BLAND_ANSWER), 0)
        self.assertEqual(score(D.PARTIAL_ANSWER), 2)
        self.assertEqual(score(D.QUOTE_ONLY_ANSWER), 0)             # quoting plan.md earns nothing
        self.assertEqual(score(D.FULL_ANSWER, plan_ok=False), 0)    # editing plan.md zeroes the run
        # each flaw is recognised on its own sentence and by nothing else
        for i, name in enumerate(D.FLAW_NAMES):
            line = D.FULL_ANSWER.splitlines()[1 + i]
            self.assertEqual([k for k, v in D.found(line).items() if v], [name], line)

    def test_doubt_plan_has_no_free_credit(self):
        import task_doubt as D
        self.assertEqual(sum(D.found(D.PLAN).values()), 0)

    def test_prereg_is_complete(self):
        for tid, t in R.TASKS.items():
            p = t["prereg"]
            self.assertGreaterEqual(p["n"], 5, tid)
            self.assertTrue(0 < p["alpha"] <= 0.05, tid)
            self.assertTrue(p["primary"], tid)
            for f in t["files"]:
                self.assertTrue(os.path.exists(f), f)

    def test_n5_can_reach_significance(self):
        # with n=5 per arm, only 5/5 vs 0/5 or 1/5, and 4/5 vs 0/5, clear alpha=0.05: say so instead of discovering it
        self.assertLessEqual(V.fisher_greater(5, 5, 1, 5), 0.05)
        self.assertLessEqual(V.fisher_greater(4, 5, 0, 5), 0.05)
        self.assertGreater(V.fisher_greater(4, 5, 1, 5), 0.05)


class ScoreMode(unittest.TestCase):
    def test_permutation_known_values(self):
        self.assertAlmostEqual(V.perm_p_greater([6] * 5, [0] * 5), 1 / 252, places=6)
        self.assertAlmostEqual(V.perm_p_greater([1, 2, 3], [1, 2, 3]), 14 / 20, places=9)   # identical groups: sums >= 6 in 14 of 20 splits
        self.assertEqual(V.perm_p_greater([0] * 4, [3] * 4), 1.0)
        self.assertAlmostEqual(V.perm_p_greater([1, 1, 0], [0, 0, 0]), 4 / 20, places=9)   # both 1s plus one 0: 4 of 20

    def test_permutation_matches_brute_force(self):
        import itertools
        a, b = [3, 1, 4, 1, 5], [2, 6, 5, 3, 0]
        pooled = a + b
        hits = sum(sum(c) >= sum(a) for c in itertools.combinations(pooled, 5))
        self.assertAlmostEqual(V.perm_p_greater(a, b), hits / len(list(itertools.combinations(pooled, 5))), places=9)

    def test_run_score_gates(self):
        cfg = {"kills": ["kills_a", "kills_b"], "gate": ["passes_real", "src_unchanged"]}
        ok = {"passes_real": True, "src_unchanged": True, "kills_a": True, "kills_b": True}
        self.assertEqual(V.run_score({"finished": True, "checks": ok}, cfg), 2)
        self.assertEqual(V.run_score({"finished": False, "checks": ok}, cfg), 0)
        self.assertEqual(V.run_score({"finished": True, "checks": {**ok, "passes_real": False}}, cfg), 0)   # failing tests do not score
        self.assertEqual(V.run_score({"finished": True, "checks": {**ok, "src_unchanged": False}}, cfg), 0)

    def rows(self, scores):
        t = "correctness-first"
        h, cfg = V.current_hashes(t), R.TASKS[t]["prereg"]["score"]
        out = []
        for arm, vals in scores.items():
            for i, k in enumerate(vals):
                checks = {"passes_real": True, "src_unchanged": True, **{f"kills_{m}": j < k for j, m in enumerate(
                    [c[6:] for c in cfg["kills"]])}}
                out.append({"task": t, "arm": arm, "i": i, "finished": True, "checks": checks, "hashes": h, "model": '["--model","x"]',
                            "git_head": "x", "date": "d", "score": k})
        return out

    def test_verdicts(self):
        t, saved = "correctness-first", R.TASKS["correctness-first"]["prereg"]["n"]
        R.TASKS[t]["prereg"]["n"] = 5
        try:
            v = lambda **s: V.verdict(t, self.rows(s))["verdict"]
            self.assertEqual(v(none=[1] * 5, skill=[5] * 5, control=[1] * 5), "WORTHWHILE")
            self.assertEqual(v(none=[1, 2, 1, 2, 1], skill=[2, 3, 2, 3, 2], control=[2, 2, 1, 2, 2]), "INCONCLUSIVE")
            self.assertEqual(v(none=[5] * 5, skill=[6] * 5, control=[5] * 5), "CEILING")
            self.assertEqual(v(none=[0] * 5, skill=[0] * 5, control=[0] * 5), "FLOOR")
            self.assertEqual(v(none=[3] * 5, skill=[2] * 5, control=[3] * 5), "NOT_WORTH")
            self.assertEqual(v(none=[1] * 5, skill=[5] * 5, control=[5] * 5), "INCONCLUSIVE")   # the control does as well
            self.assertEqual(V.verdict(t, self.rows({"none": [1] * 5, "skill": [5] * 5}))["verdict"], "INCOMPLETE")
        finally:
            R.TASKS[t]["prereg"]["n"] = saved

    def test_stored_score_must_match_checks(self):
        saved, tmp = V.LEDGER, tempfile.mkdtemp()
        V.LEDGER = tmp
        try:
            rows = self.rows({"none": [1], "skill": [5], "control": [1]})
            rows[1]["score"] = 6                          # a hand-edited score
            with open(os.path.join(tmp, "correctness-first.jsonl"), "w") as f:
                f.write("\n".join(json.dumps(r) for r in rows) + "\n")
            self.assertIn("stored score", " ".join(V.validate_ledger()))
        finally:
            V.LEDGER = saved
            shutil.rmtree(tmp)


class Fisher(unittest.TestCase):
    def test_known_values(self):
        self.assertAlmostEqual(V.fisher_greater(5, 5, 0, 5), 1 / 252, places=6)
        self.assertAlmostEqual(V.fisher_greater(3, 3, 0, 3), 0.05, places=6)
        self.assertEqual(V.fisher_greater(0, 5, 0, 5), 1.0)
        self.assertEqual(V.fisher_greater(0, 5, 5, 5), 1.0)

    def test_monotone(self):
        for c in range(6):
            ps = [V.fisher_greater(a, 5, c, 5) for a in range(6)]
            self.assertEqual(ps, sorted(ps, reverse=True))


def _rows(task_id, passes, model='["--model","p/m"]', hashes=None):
    t = R.TASKS[task_id]
    h = hashes or V.current_hashes(task_id)
    good = {c: True for c in t["prereg"]["primary"]}
    bad = {**good, t["prereg"]["primary"][0]: False}
    out = []
    for arm, k in passes.items():
        for i in range(t["prereg"]["n"]):
            out.append({"task": task_id, "arm": arm, "i": i, "finished": True, "checks": good if i < k else bad,
                        "hashes": h, "model": model, "git_head": "x", "date": "2026-01-01"})
    return out


class Verdicts(unittest.TestCase):
    T = "property-tests"

    def setUp(self):   # the cases below are written for 5 runs per arm, whatever the task registers
        self._n = R.TASKS[self.T]["prereg"]["n"]
        R.TASKS[self.T]["prereg"]["n"] = 5

    def tearDown(self):
        R.TASKS[self.T]["prereg"]["n"] = self._n

    def v(self, **passes):
        return V.verdict(self.T, _rows(self.T, passes))["verdict"]

    def test_verdicts(self):
        self.assertEqual(V.verdict(self.T, [])["verdict"], "NOT_RUN")
        self.assertEqual(self.v(none=0, skill=5, control=0), "WORTHWHILE")
        self.assertEqual(self.v(none=0, skill=5, control=4), "INCONCLUSIVE")     # the control does nearly as well
        self.assertEqual(self.v(none=4, skill=5, control=0), "CEILING")
        self.assertEqual(self.v(none=2, skill=2, control=0), "NOT_WORTH")
        self.assertEqual(self.v(none=0, skill=0, control=0), "FLOOR")                # nobody passes: the task shows nothing
        self.assertEqual(self.v(none=1, skill=3, control=0), "INCONCLUSIVE")
        rows = _rows(self.T, {"none": 0, "skill": 5})
        self.assertEqual(V.verdict(self.T, rows)["verdict"], "INCOMPLETE")

    def test_models_are_never_mixed(self):
        a = _rows(self.T, {"none": 0, "skill": 5, "control": 0}, model='["--model","p/strong"]')
        b = _rows(self.T, {"none": 4, "skill": 5, "control": 4}, model='["--model","p/weak"]')
        self.assertEqual(V.verdict(self.T, a + b)["verdict"], "MIXED_MODELS")          # no model given: refuse to pool
        by = {V.model_name(v["model"]): v["verdict"] for v in V.verdict_all(self.T, a + b)}
        self.assertEqual(by, {"p/strong": "WORTHWHILE", "p/weak": "CEILING"})
        self.assertEqual(V.verdict(self.T, a + b, model='["--model","p/strong"]')["score"]["none"], (0, 5))
        self.assertEqual(V.model_name('["-e","x","--model","p/m","--thinking","medium"]'), "p/m (thinking medium)")

    def test_stale_rows_do_not_count(self):
        rows = _rows(self.T, {"none": 0, "skill": 5, "control": 0}, hashes={"skill": "old", "control": "x", "task": "y"})
        v = V.verdict(self.T, rows)
        self.assertEqual((v["verdict"], v["stale_rows"]), ("NOT_RUN", 15))

    def test_unfinished_run_fails(self):
        rows = _rows(self.T, {"none": 2, "skill": 5, "control": 0})
        for r in rows:
            if r["arm"] == "skill":
                r["finished"] = False
        self.assertEqual(V.verdict(self.T, rows)["verdict"], "NOT_WORTH")

    def test_hash_changes_when_skill_changes(self):
        h = V.skill_hash("property-tests")
        p = os.path.join(V.ROOT, "skills", "property-tests", "SKILL.md")
        orig = V._read(p)
        try:
            with open(p, "ab") as f:
                f.write(b"\n")
            self.assertNotEqual(V.skill_hash("property-tests"), h)
        finally:
            with open(p, "wb") as f:
                f.write(orig)
        self.assertEqual(V.skill_hash("property-tests"), h)


class Ledger(unittest.TestCase):
    def setUp(self):
        self.saved, self.tmp = V.LEDGER, tempfile.mkdtemp()
        V.LEDGER = self.tmp

    def tearDown(self):
        V.LEDGER = self.saved
        shutil.rmtree(self.tmp)

    def write(self, rows, name="property-tests"):
        with open(os.path.join(self.tmp, f"{name}.jsonl"), "w") as f:
            f.write("\n".join(json.dumps(r) for r in rows) + "\n")

    def test_clean(self):
        self.write(_rows("property-tests", {"none": 0, "skill": 5, "control": 0}))
        self.assertEqual(V.validate_ledger(), [])

    def test_tampering_is_reported(self):
        rows = _rows("property-tests", {"none": 0, "skill": 5, "control": 0})
        rows[0]["model"] = "default"                          # model not pinned
        del rows[1]["git_head"]                              # missing field
        rows.append(dict(rows[2]))                           # duplicate run
        rows[3]["checks"] = {}                               # primary checks not graded
        self.write(rows)
        probs = " ".join(V.validate_ledger())
        for s in ("model not pinned", "missing", "duplicate run", "primary checks not graded"):
            self.assertIn(s, probs)

    def test_same_run_index_under_two_models_is_not_a_duplicate(self):
        a = _rows("property-tests", {"none": 0, "skill": 5, "control": 0}, model='["--model","p/a"]')
        b = _rows("property-tests", {"none": 0, "skill": 5, "control": 0}, model='["--model","p/b"]')
        self.write(a + b)
        self.assertEqual(V.validate_ledger(), [])
        self.write(a + a[:1])                                 # a true duplicate is still reported
        self.assertIn("duplicate run", " ".join(V.validate_ledger()))

    def test_unknown_task_file(self):
        self.write([], name="nope")
        self.assertIn("no task", " ".join(V.validate_ledger()))

    def test_committed_ledger_is_valid(self):
        V.LEDGER = self.saved
        self.assertEqual(V.validate_ledger(), [])


class EndToEndWithFakeAgent(unittest.TestCase):
    """run.py's per-run path (session -> grade -> ledger row) with a fake `pi`: rows are valid and the verdict follows."""

    def test_rows_and_verdict(self):
        import run as RUN
        tmp = tempfile.mkdtemp()
        saved = (V.LEDGER, RUN.FULL, RUN.ARMS_DIR, os.environ.get("EVAL_PI"), os.environ.get("FAKE_TASK"))
        V.LEDGER, RUN.FULL, RUN.ARMS_DIR = os.path.join(tmp, "ledger"), os.path.join(tmp, "full"), os.path.join(tmp, "arms")
        os.makedirs(V.LEDGER); os.makedirs(RUN.FULL)
        os.environ["EVAL_PI"] = os.path.join(HERE, "fake_agent.py")
        os.environ["FAKE_TASK"] = tid = "property-tests"
        try:
            h = V.current_hashes(tid)
            for arm in V.ARMS:
                d = RUN.build_arm(R.TASKS[tid]["skill"], arm)
                for i in range(R.TASKS[tid]["prereg"]["n"]):
                    RUN.one(tid, arm, i, d, '["--model","fake/agent"]', "head", h)
            self.assertEqual(V.validate_ledger(), [])
            v = V.verdict(tid)
            n = R.TASKS[tid]["prereg"]["n"]
            self.assertEqual((v["verdict"], v["score"]["skill"], v["score"]["none"]), ("WORTHWHILE", (n, n), (0, n)))
            control = open(os.path.join(RUN.ARMS_DIR, f"{R.TASKS[tid]['skill']}-control", "SKILL.md")).read()
            real = open(os.path.join(V.ROOT, "skills", R.TASKS[tid]["skill"], "SKILL.md")).read()
            self.assertLessEqual(abs(len(control) - len(real)), 2)
            self.assertEqual(control.split("\n---", 1)[0], real.split("\n---", 1)[0])   # same name + description
        finally:
            V.LEDGER, RUN.FULL, RUN.ARMS_DIR = saved[:3]
            for k, val in (("EVAL_PI", saved[3]), ("FAKE_TASK", saved[4])):
                if val is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = val
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_run_where_the_agent_never_answers_is_not_recorded(self):
        import run as RUN
        tmp = tempfile.mkdtemp()
        saved = (V.LEDGER, RUN.FULL, os.environ.get("EVAL_PI"))
        V.LEDGER, RUN.FULL = os.path.join(tmp, "ledger"), os.path.join(tmp, "full")
        os.makedirs(V.LEDGER); os.makedirs(RUN.FULL)
        dead = os.path.join(tmp, "dead_pi.sh")
        with open(dead, "w") as f:
            f.write("#!/bin/sh\nexit 1\n")
        os.chmod(dead, 0o755)
        os.environ["EVAL_PI"] = dead
        try:
            with self.assertRaises(RuntimeError):
                RUN.one("property-tests", "none", 0, None, '["--model","x"]', "head", V.current_hashes("property-tests"))
            self.assertEqual(os.listdir(V.LEDGER), [])
        finally:
            V.LEDGER, RUN.FULL = saved[:2]
            if saved[2] is None:
                os.environ.pop("EVAL_PI", None)
            else:
                os.environ["EVAL_PI"] = saved[2]
            shutil.rmtree(tmp, ignore_errors=True)

    def test_a_silent_agent_times_out_instead_of_hanging(self):
        import time
        import run as RUN
        tmp = tempfile.mkdtemp()
        silent = os.path.join(tmp, "silent_pi.sh")
        with open(silent, "w") as f:
            f.write("#!/bin/sh\nsleep 60\n")
        os.chmod(silent, 0o755)
        saved = os.environ.get("EVAL_PI")
        os.environ["EVAL_PI"] = silent
        try:
            t0 = time.time()
            log, usage, finished = RUN.session(tmp, None, "hi", lambda n, t: None, turn_timeout=2)
            self.assertFalse(finished)
            self.assertLess(time.time() - t0, 20)
        finally:
            if saved is None:
                os.environ.pop("EVAL_PI", None)
            else:
                os.environ["EVAL_PI"] = saved
            shutil.rmtree(tmp, ignore_errors=True)

    def test_none_arm_prompt_does_not_name_a_missing_skill(self):
        import task_property_tests as P
        self.assertNotIn("skill", P.PROMPT_NONE.lower())
        self.assertTrue(P.PROMPT.startswith("Use the property-tests-from-examples skill."))
        self.assertTrue(P.PROMPT.endswith(P.PROMPT_NONE))      # otherwise identical

    def test_correctness_first_none_arm_prompt(self):
        import task_correctness_first as C
        self.assertNotIn("skill", C.PROMPT_NONE.lower())
        self.assertTrue(C.PROMPT.endswith(C.PROMPT_NONE) and C.PROMPT.startswith("Use the correctness-first-design skill."))

    def test_preflight_refuses_unpinned_model(self):
        import run as RUN
        saved = os.environ.pop("EVAL_PI_ARGS", None)
        try:
            with self.assertRaises(SystemExit):
                RUN.preflight(["property-tests"])
        finally:
            if saved is not None:
                os.environ["EVAL_PI_ARGS"] = saved


if __name__ == "__main__":
    unittest.main(verbosity=1)
