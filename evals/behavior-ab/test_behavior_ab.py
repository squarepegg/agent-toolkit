#!/usr/bin/env python3
"""Offline sensitivity, properties and end-to-end RPC tests. No model calls and no live ledger writes."""
import copy
import importlib.util
import contextlib
import io
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location("behavior_runner", HERE/"run.py")
R = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R)
T = R.T


def good_rows(task):
    return [{"task": task, "case": c["id"], "split": c["split"], "arm": a, "i": i,
             "finished": True, "checks": {k: True for k in T.primary(task, c)}, "pass": True, "cost_usd": .01}
            for a in R.ARMS for i in range(R.PLAN["repeats"]) for c in T.CASES[task]]


def scores(task, passes):
    rows = good_rows(task)
    for r in rows:
        if r["i"] >= passes[r["arm"]]:
            r["checks"]["app_unchanged"] = False
    return rows


class Arms(unittest.TestCase):
    def test_baselines_are_byte_exact_and_controls_match_candidate_lengths(self):
        for task, c in R.CANDIDATES.items():
            p = HERE/"baselines"/c["base"]/("SKILL.md" if c["base"] == "spec-first" else "skills/brain/SKILL.md")
            self.assertEqual(R.skill_text(task, "baseline"), p.read_text())
            candidate, control = R.skill_text(task, "candidate"), R.skill_text(task, "control")
            self.assertEqual(len(candidate), len(control), task)
            self.assertIn(p.read_text(), control, task)
            if task != "activation":
                self.assertTrue(candidate.startswith(p.read_text()))

    def test_snapshots_match_provenance(self):
        provenance = json.loads((HERE/"baselines/provenance.json").read_text())
        for rel, source in provenance["files"].items():
            self.assertEqual(R.hashlib.sha256((HERE/"baselines"/rel).read_bytes()).hexdigest(), source["sha256"], rel)


class VerdictProperties(unittest.TestCase):
    def test_verdict_examples(self):
        for task in R.PLAN["candidates"]:
            for passes, expected in [({"baseline":0,"candidate":5,"control":0}, "WORTHWHILE"),
                                     ({"baseline":4,"candidate":5,"control":0}, "CEILING"),
                                     ({"baseline":0,"candidate":0,"control":0}, "FLOOR"),
                                     ({"baseline":1,"candidate":1,"control":0}, "NOT_WORTH"),
                                     ({"baseline":0,"candidate":5,"control":5}, "INCONCLUSIVE")]:
                self.assertEqual(R.summarize(task, scores(task, passes))["verdict"], expected)
            self.assertEqual(R.summarize(task, [])["verdict"], "NOT_RUN")
            self.assertEqual(R.summarize(task, good_rows(task)[:-1])["verdict"], "INCOMPLETE")

    def test_order_invariance_and_cached_pass_is_not_trusted(self):
        for task in R.PLAN["candidates"]:
            rows = scores(task, {"baseline":0,"candidate":5,"control":0})
            expected = R.summarize(task, rows)
            for seed in range(25):
                shuffled = copy.deepcopy(rows)
                random.Random(seed).shuffle(shuffled)
                for r in shuffled:
                    r["pass"] = not r["pass"]
                self.assertEqual(R.summarize(task, shuffled), expected)

    def test_every_check_is_required_and_all_corruptions_block_adoption(self):
        for task in R.PLAN["candidates"]:
            reference = scores(task, {"baseline":0,"candidate":5,"control":0})
            for key in reference[-1]["checks"]:
                rows = copy.deepcopy(reference)
                del rows[-1]["checks"][key]
                self.assertEqual(R.summarize(task, rows)["verdict"], "INVALID_LEDGER", key)
            for key, value in [("cost_usd", float("nan")), ("cost_usd", -1), ("i", 999),
                               ("split", "invented"), ("checks", {}), ("finished", 1)]:
                rows = copy.deepcopy(reference)
                rows[-1][key] = value
                self.assertEqual(R.summarize(task, rows)["verdict"], "INVALID_LEDGER", (key,value))
            self.assertEqual(R.summarize(task, reference+[reference[0]])["verdict"], "INVALID_LEDGER")

    def test_expense_and_missing_prices_block_adoption(self):
        for task in R.PLAN["candidates"]:
            rows = scores(task, {"baseline":0,"candidate":5,"control":0})
            for r in rows:
                if r["arm"] == "candidate":
                    r["cost_usd"] *= 2
            self.assertEqual(R.summarize(task, rows)["verdict"], "TOO_COSTLY")
            for r in rows:
                r["cost_usd"] = 0
            self.assertEqual(R.summarize(task, rows)["verdict"], "COST_UNVERIFIED")


class GraderSensitivity(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp)

    def fixture(self, task, case):
        root = self.tmp/task/case["id"]
        T.setup(root, task, case)
        return root

    def test_wrong_activation_or_no_answer_cannot_pass(self):
        for c in T.CASES["activation"]:
            root = self.fixture("activation", c)
            skill = self.tmp/"SKILL.md"
            tool = {"name":"read", "args":{"path":str(skill)}}
            answer = "Which plans should be supported?" if c["activate"] else ('{"total":80}' if c["id"]=="explain" else '{"allowed":false}')
            tools = [tool] if c["activate"] else []
            log = [{"assistant":answer,"tools":tools}]
            self.assertTrue(all(T.grade(root,"activation",c,log,skill).values()))
            log[0]["tools"] = [] if tools else [tool]
            self.assertFalse(T.grade(root,"activation",c,log,skill)["correct_activation"])

    def test_stopping_rejects_extra_question_postscript_and_new_code(self):
        for c in T.CASES["stopping"]:
            root = self.fixture("stopping", c)
            logs = [{"assistant":"Which audience?", "tools":[]} for _ in range(2)]
            self.assertTrue(all(T.grade(root,"stopping",c,logs,self.tmp, [True,True]).values()))
            for mutant in ["Which audience? Which channel?", "Which audience? Here is my answer.", "I have finished."]:
                bad = copy.deepcopy(logs); bad[0]["assistant"] = mutant
                self.assertFalse(all(T.grade(root,"stopping",c,bad,self.tmp,[True,True]).values()), mutant)
            T.put(root,"new_feature.py","print('implemented')")
            self.assertFalse(T.grade(root,"stopping",c,logs,self.tmp)["app_unchanged"])

    def test_recovery_rejects_each_lost_or_invented_fact(self):
        for c in T.CASES["recovery"]:
            root = self.fixture("recovery",c)
            obj = {"next_action":"ask_owner", "passed_checks":["python3 verify.py unit"],
                   "failed_checks":["python3 verify.py integration"],"unrun_checks":["browser smoke test"],
                   "open_decisions":[c["decision"]],"constraints":[c["limit"]],"allowed_to_ship":False}
            grade = lambda o: T.grade(root,"recovery",c,[{"assistant":json.dumps(o),"tools":[]}],self.tmp)
            self.assertTrue(all(grade(obj).values()))
            for key in obj:
                bad = copy.deepcopy(obj)
                bad[key] = True if key=="allowed_to_ship" else ("ship" if key=="next_action" else [])
                self.assertFalse(all(grade(bad).values()), key)

    def test_sampling_has_evidence_no_false_alarm_and_budget(self):
        for c in T.CASES["revalidation"]:
            root = self.fixture("revalidation",c)
            for fid in "ab":
                subprocess.run([sys.executable,"source.py",fid],cwd=root,check=True,capture_output=True)
            obj = {"reviewed":["a","b"],"contradicted":sorted(set("ab")&set(c["stale"])),"unreviewed":["c","d"]}
            grade = lambda o: T.grade(root,"revalidation",c,[{"assistant":json.dumps(o),"tools":[]}],self.tmp)
            self.assertTrue(all(grade(obj).values()))
            for key in obj:
                bad = copy.deepcopy(obj); bad[key] = []
                self.assertFalse(all(grade(bad).values()), key)
            bad = copy.deepcopy(obj); bad["contradicted"] = ["a","b"]
            self.assertFalse(grade(bad)["found_stale_fact"])
            subprocess.run([sys.executable,"source.py","c"],cwd=root,check=True,capture_output=True)
            self.assertFalse(grade(obj)["within_budget"])


class Transport(unittest.TestCase):
    def test_reference_and_naive_runs_for_every_case(self):
        for task in R.PLAN["candidates"]:
            for case in T.CASES[task]:
                for mode in ("reference", "naive"):
                    with tempfile.TemporaryDirectory() as tmp:
                        request = {"task":task,"case":case,"arm":"candidate", "pi_args":["--model","fake/agent"],"workdir":tmp}
                        env = {**os.environ,"EVAL_PI":str(HERE/"fake_pi.py"),"BEHAVIOR_FAKE_MODE":mode}
                        p = subprocess.run([sys.executable,str(HERE/"run.py"),"worker"],input=json.dumps(request),
                                           env=env,capture_output=True,text=True,timeout=15)
                        self.assertEqual(p.returncode,0,p.stderr)
                        result = json.loads(p.stdout)
                        self.assertEqual(set(result["checks"]),T.primary(task,case))
                        self.assertEqual(result["pass"],mode=="reference",(task,case,mode,result["checks"]))
                        if task=="recovery":
                            self.assertNotEqual(result["log"][0]["user"],result["save_log"][0]["user"])
                            self.assertNotIn(case["decision"],result["log"][0]["user"])

    def test_stalled_process_is_bounded(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            R.bounded_process([sys.executable,"-c","import time; time.sleep(60)"],timeout=.1)

    def test_dead_provider_does_not_create_evidence(self):
        with patch.dict(os.environ,{"EVAL_PI":str(HERE/"fake_pi.py"),"BEHAVIOR_FAKE_MODE":"dead"}):
            with self.assertRaises(RuntimeError):
                R.preflight({"pi_args":["--model","fake/agent"]})


class Registration(unittest.TestCase):
    def test_report_is_portable_and_invalid_live_rows_fail_the_gate(self):
        reg = {"pi_args":["--model","fake/agent"]}
        rows = good_rows("activation")
        for row in rows:
            row.update(experiment=R.digest(reg),model=reg["pi_args"])
        with patch.object(R,"rows",return_value=rows), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(R.report(reg),0)
            rows[0]["checks"] = {}
            self.assertEqual(R.report(reg),1)
        with patch.object(R,"REG") as path, patch.object(R,"hashes",return_value={}), patch.object(R,"committed"), \
             patch.object(R,"extension_hashes",side_effect=AssertionError("CI must not need a local provider")):
            path.exists.return_value=True
            path.read_text.return_value=json.dumps({"inputs":{},"pi_args":[],"extensions":{}})
            R.registration(verify_provider=False)

    def test_uncommitted_and_hidden_edits_are_refused(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data, reg = root/"data", root/"registration.json"
            data.write_bytes(b"registered input")
            reg.write_text("{}")
            subprocess.run(["git","init","-q"],cwd=root,check=True)
            subprocess.run(["git","add","."],cwd=root,check=True)
            subprocess.run(["git","-c","user.name=Eval","-c","user.email=eval@example.com","commit","-qm","registered"],cwd=root,check=True)
            with patch.object(R,"ROOT",root), patch.object(R,"REG",reg), patch.object(R,"inputs",return_value=[data]):
                R.committed()
                subprocess.run(["git","update-index","--skip-worktree","data"],cwd=root,check=True)
                for mutation in (b"",b"different",b"\x00", "uncommitted \u2603".encode()):
                    data.write_bytes(mutation)
                    with self.assertRaises(SystemExit):
                        R.committed()
                data.write_bytes(b"registered input")
                R.committed()


if __name__ == "__main__":
    unittest.main()
