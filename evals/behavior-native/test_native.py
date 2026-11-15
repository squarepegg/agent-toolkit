import importlib.util
import copy
import random
import unittest
from pathlib import Path

HERE=Path(__file__).resolve().parent
s=importlib.util.spec_from_file_location("native_behavior",HERE/"native.py");N=importlib.util.module_from_spec(s);s.loader.exec_module(N)

def rows(task,counts):
    out=[]
    for arm in N.PLAN["arms"]:
        for i in range(N.PLAN["n"]):
            case=N.T.CASES[task][N.PLAN["case_sequences"][task][i]]
            keys=N.T.primary(task,case)-({"correct_activation"} if task=="activation" else set())
            checks={k:True for k in keys};checks["app_unchanged"]=i<counts[arm]
            out.append(dict(task=task,model=N.PLAN["models"][0],arm=arm,i=i,case=case["id"],split=case["split"],checks=checks,**{"pass":all(checks.values())}))
    return out

class Native(unittest.TestCase):
    def test_verdicts(self):
        for task in N.PLAN["tasks"]:
            for counts,verdict in [([0,5,0],"QUALITY_GAIN_COST_UNVERIFIED"),([5,5,5],"CEILING"),([0,0,0],"FLOOR"),([0,5,5],"INCONCLUSIVE"),([1,0,0],"NO_GAIN")]:
                rs=rows(task,dict(zip(N.PLAN["arms"],counts)))
                self.assertEqual(N.summary(task,N.PLAN["models"][0],rs)["verdict"],verdict)
    def test_permutations_and_cached_pass_do_not_change_results(self):
        for task in N.PLAN["tasks"]:
            rs=rows(task,dict(zip(N.PLAN["arms"],[0,5,0])))
            expected=N.summary(task,N.PLAN["models"][0],rs)
            for seed in range(20):
                shuffled=copy.deepcopy(rs);random.Random(seed).shuffle(shuffled)
                for r in shuffled:r["pass"]=not r["pass"]
                self.assertEqual(N.summary(task,N.PLAN["models"][0],shuffled),expected)
    def test_missing_checks_and_duplicates_block_verdicts(self):
        for task in N.PLAN["tasks"]:
            rs=rows(task,dict(zip(N.PLAN["arms"],[0,5,0])))
            for key in rs[0]["checks"]:
                bad=copy.deepcopy(rs);del bad[0]["checks"][key]
                self.assertEqual(N.summary(task,N.PLAN["models"][0],bad)["verdict"],"INVALID_LEDGER")
            self.assertEqual(N.summary(task,N.PLAN["models"][0],rs+[rs[0]])["verdict"],"INVALID_LEDGER")
    def test_second_model_is_not_pooled(self):
        task="activation";rs=rows(task,dict(zip(N.PLAN["arms"],[0,5,0])))
        self.assertEqual(N.summary(task,N.PLAN["models"][1],rs)["verdict"],"NOT_RUN")

if __name__=="__main__":unittest.main()
