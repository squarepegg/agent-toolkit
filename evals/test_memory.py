#!/usr/bin/env python3
"""Tests for evals/memory.py and its use by gate.py (stdlib, no model). python3 evals/test_memory.py"""
import copy, json, os, sys, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import gate, memory as mem  # noqa: E402
from test_gate import C, res  # noqa: E402


def row(id, verdict, skill="s", problem="p", model="m", date="2026-01-01", **kw):
    return {"id": id, "date": date, "skill": skill, "problem": problem, "change": "c", "rationale": "r", "observed": "o",
            "verdict": verdict, "model": model, **kw}


class Ledger(unittest.TestCase):
    def test_committed_ledger_is_valid_and_report_current(self):
        rows = mem.load()
        self.assertGreaterEqual(len(rows), 13)
        self.assertEqual(open(mem.REPORT).read(), mem.report(rows))

    def test_validation_rejects(self):
        good = row("a", "CEILING")
        mem.validate([good])
        bad = [("unknown key", {**good, "x": 1}), ("verdict", {**good, "verdict": "MEH"}), ("empty field", {**good, "change": " "}),
               ("date", {**good, "date": "11/15"}), ("matched without predicted", {**good, "matched": "yes"}),
               ("matched value", {**good, "predicted": "p", "matched": "maybe"}), ("cost", {**good, "cost_usd": -1}),
               ("model type", {**good, "model": 3})]
        for name, r in bad:
            with self.assertRaises(mem.BadLedger, msg=name):
                mem.validate([r])
        with self.assertRaises(mem.BadLedger):
            mem.validate([good, copy.deepcopy(good)])                       # duplicate id
        with self.assertRaises(mem.BadLedger):
            mem.validate([row("a", "CEILING", date="2026-02-01"), row("b", "CEILING", date="2026-01-01")])  # append-only order


class Rule(unittest.TestCase):
    def rep(self, rows, **kw):
        return [r["id"] for r in mem.repeat_of(rows, kw.pop("skill", "s"), kw.pop("problem", "p"), kw.pop("model", "m"), **kw)]

    def test_two_informative_failures_trigger(self):
        self.assertEqual(self.rep([row("a", "CEILING")]), [])
        self.assertEqual(self.rep([row("a", "CEILING"), row("b", "NO_DELTA")]), ["a", "b"])

    def test_experiment_failures_do_not_count(self):
        neutral = [row(str(i), v) for i, v in enumerate(["UNSOUND_GRADER", "CONFOUNDED", "INVALID_MODEL", "INCONCLUSIVE"])]
        self.assertEqual(self.rep(neutral), [])
        self.assertEqual(self.rep([row("a", "CEILING")] + neutral), [])

    def test_new_evidence_allows_the_attempt(self):
        rows = [row("a", "CEILING"), row("b", "FLOOR")]
        self.assertEqual(self.rep(rows, retry_because="harder probes"), [])
        self.assertEqual(self.rep(rows, retry_because="  "), ["a", "b"])      # blank is not a reason
        self.assertEqual(self.rep(rows, model="other"), [])                   # another model is new evidence
        self.assertEqual(self.rep(rows, problem="q"), [])
        self.assertEqual(self.rep(rows, skill="t"), [])

    def test_unknown_model_matches_any(self):
        rows = [row("a", "CEILING", model=None), row("b", "CEILING", model=None)]
        self.assertEqual(self.rep(rows, model="whatever"), ["a", "b"])
        self.assertEqual(self.rep([row("a", "CEILING"), row("b", "CEILING")], model=None), ["a", "b"])

    def test_adopt_closes_the_corner(self):
        rows = [row("a", "CEILING"), row("b", "CEILING"), row("c", "ADOPT"), row("d", "FLOOR")]
        self.assertEqual(self.rep(rows), [])
        self.assertEqual(self.rep(rows + [row("e", "FLOOR")]), ["d", "e"])

    def test_backtest_uses_only_earlier_rows(self):
        rows = [row("a", "CEILING"), row("b", "CEILING"), row("c", "CEILING", retry_because="x"), row("d", "ADOPT")]
        fired = mem.backtest(rows)
        self.assertEqual([(x["id"], x["justified"]) for x in fired], [("c", True), ("d", False)])

    def test_real_history_pin(self):
        # the corner the log itself calls "third miss in the same corner" is the one the trigger fires on
        fired = {x["id"] for x in mem.backtest(mem.load())}
        self.assertIn("cf-v3-pilot", fired)
        self.assertNotIn("doubt-v2-haiku", fired)           # different skill and model


class GateUse(unittest.TestCase):
    cand = {**C, "problem": "p"}
    good = res([2, 2, 2], [0, 0, 0])
    hist = [row("a", "CEILING", skill="dst", model="m/x"), row("b", "FLOOR", skill="dst", model="m/x")]

    def test_adopt_becomes_repeat_without_a_reason(self):
        v, f = gate.decide(self.cand, self.good, self.hist)
        self.assertEqual((v, f["repeat_of"]), ("REPEAT", ["a", "b"]))

    def test_each_escape(self):
        self.assertEqual(gate.decide({**self.cand, "retry_because": "new probes"}, self.good, self.hist)[0], "ADOPT")
        self.assertEqual(gate.decide(self.cand, self.good, self.hist[:1])[0], "ADOPT")
        self.assertEqual(gate.decide({**self.cand, "problem": "other"}, self.good, self.hist)[0], "ADOPT")
        self.assertEqual(gate.decide(C, self.good, self.hist)[0], "ADOPT")           # no `problem`: ledger not consulted
        self.assertEqual(gate.decide(self.cand, self.good)[0], "ADOPT")              # no ledger passed
        other_model = copy.deepcopy(self.good); other_model["pi_args"] = ["--model", "n/y"]
        self.assertEqual(gate.decide(self.cand, other_model, self.hist)[0], "ADOPT")

    def test_repeat_never_hides_a_real_failure(self):
        self.assertEqual(gate.decide(self.cand, res([1, 1], [1, 1]), self.hist)[0], "NO_DELTA")

    def test_bad_memory_keys(self):
        for k in ("problem", "retry_because"):
            with self.assertRaises(gate.BadInput):
                gate.validate_candidate({**C, k: ""})

    def test_ledger_row_is_a_valid_append(self):
        v, f = gate.decide(self.cand, self.good, self.hist)
        line = mem.ledger_line(self.cand, self.good, v, f, gate.model_of(self.good), "2026-01-02")
        mem.validate([row("z", "ADOPT", date="2026-01-01")] + [json.loads(line)])


if __name__ == "__main__":
    unittest.main()
