"""Offline body/control, artifact-grader calibration and verdict invariants. No model calls."""
import copy
import importlib.util
import random
import tempfile
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("native_skill_study",HERE/"study.py");S=importlib.util.module_from_spec(spec);spec.loader.exec_module(S)


def observations(task,scores):
    out=[];kills=[k for k in S.expected_keys(task) if k.startswith("kills_")]
    for arm,values in scores.items():
        for i,value in enumerate(values):
            checks={k:True for k in S.expected_keys(task)}
            for j,key in enumerate(sorted(kills)):checks[key]=j<value
            out.append(dict(id=f"{arm}-{i}",task=task,model=S.PLAN["models"][0],arm=arm,i=i,checks=checks,score=value,exposure_status="in_scope_self_reported"))
    return out


class Study(unittest.TestCase):
    def test_actual_skill_and_control_are_equal_length_and_none_has_no_body(self):
        for task in S.PLAN["tasks"]:
            real=S.arm_text(task,"skill");control=S.arm_text(task,"control")
            self.assertIsNone(S.arm_text(task,"none"))
            self.assertEqual(len(real),len(control))
            self.assertEqual(real.split("\n---",1)[0],control.split("\n---",1)[0])

    def test_existing_graders_distinguish_reference_naive_and_untouched_artifacts(self):
        for task in S.PLAN["tasks"]:
            t=S.R.TASKS[task]
            for which in ("reference","naive","untouched"):
                with tempfile.TemporaryDirectory() as tmp:
                    t["setup"](tmp)
                    if which!="untouched":t["write_solution"](tmp,which)
                    checks,detail,score=S.grade_artifact(task,Path(tmp),"")
                    self.assertTrue(checks["passes_real"],(task,which,detail))
                    self.assertTrue(checks["real_replays"],(task,which))
                    self.assertTrue(checks["src_unchanged"],(task,which))
                    self.assertEqual(set(checks),S.expected_keys(task))
                    if which=="reference":self.assertEqual(score,S.PLAN["test_faults"][task])
                    else:self.assertLess(score,S.PLAN["test_faults"][task])

    def test_replay_and_source_guards_cannot_be_ignored(self):
        for task in S.PLAN["tasks"]:
            row=observations(task,{"skill":[S.PLAN["test_faults"][task]]})[0]
            for gate in ("passes_real","src_unchanged","real_replays"):
                bad=copy.deepcopy(row);bad["checks"][gate]=False
                self.assertEqual(S.score(bad),0)
        self.assertFalse(S.clean_outcomes({"__timeout__":True}))
        self.assertFalse(S.clean_outcomes({"__crash__":"oops"}))
        self.assertFalse(S.clean_outcomes({"test":"FAIL"}))

    def test_ceiling_rule_is_predefined_and_requires_two_full_baselines(self):
        for task,mx in S.PLAN["test_faults"].items():
            model=S.PLAN["models"][0]
            rows=observations(task,{a:[mx,mx] for a in S.PLAN["arms"]})
            self.assertEqual(S.cell(task,model,rows)["verdict"],"CEILING")
            rows=observations(task,{"none":[mx,mx-1],"skill":[mx,mx],"control":[mx,mx]})
            self.assertEqual(S.cell(task,model,rows)["verdict"],"INCOMPLETE")

    def test_verdicts_model_separation_and_order_invariance(self):
        for task,mx in S.PLAN["test_faults"].items():
            model=S.PLAN["models"][0]
            good=observations(task,{"none":[0]*5,"skill":[mx]*5,"control":[0]*5})
            self.assertEqual(S.cell(task,model,good)["verdict"],"QUALITY_GAIN_COST_UNKNOWN")
            self.assertEqual(S.cell(task,S.PLAN["models"][1],good)["verdict"],"NOT_RUN")
            expected=S.cell(task,model,good)
            for seed in range(20):
                shuffled=copy.deepcopy(good);random.Random(seed).shuffle(shuffled)
                self.assertEqual(S.cell(task,model,shuffled),expected)
            for values,verdict in [({"none":[0]*5,"skill":[mx]*5,"control":[mx]*5},"INCONCLUSIVE"),
                                   ({"none":[0]*5,"skill":[0]*5,"control":[0]*5},"FLOOR")]:
                self.assertEqual(S.cell(task,model,observations(task,values))["verdict"],verdict)

    def test_missing_checks_duplicates_and_reported_contamination_block_positive_results(self):
        for task,mx in S.PLAN["test_faults"].items():
            rows=observations(task,{"none":[0]*5,"skill":[mx]*5,"control":[0]*5});model=S.PLAN["models"][0]
            for key in rows[0]["checks"]:
                bad=copy.deepcopy(rows);del bad[0]["checks"][key]
                self.assertEqual(S.cell(task,model,bad)["verdict"],"INVALID_LEDGER")
            self.assertEqual(S.cell(task,model,rows+[rows[0]])["verdict"],"INVALID_LEDGER")
            bad=copy.deepcopy(rows);bad[0]["exposure_status"]="unverified_or_contaminated"
            self.assertEqual(S.cell(task,model,bad)["verdict"],"EXPOSURE_UNVERIFIED")


if __name__=="__main__":unittest.main()
