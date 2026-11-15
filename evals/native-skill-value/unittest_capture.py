"""Structured unittest results: subtest failures are real failures, even without a final '... FAIL' parent line.

Run in a grading copy. Fail fast avoids enormous traceback output on exhaustive generated rows. Return the same
status dictionary interface as the existing outcome harness; no model-authored code or expectations are changed.
"""
import json
import os
import subprocess
import sys

RUNNER = r'''
import io, json, unittest
class Result(unittest.TextTestResult):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw); self.outcomes={}
    def addSuccess(self,test):
        self.outcomes.setdefault(test.id(),"ok"); super().addSuccess(test)
    def addFailure(self,test,err):
        self.outcomes[test.id()]="FAIL"; super().addFailure(test,err)
    def addError(self,test,err):
        self.outcomes[test.id()]="ERROR"; super().addError(test,err)
    def addSkip(self,test,reason):
        self.outcomes.setdefault(test.id(),"skipped"); super().addSkip(test,reason)
    def addExpectedFailure(self,test,err):
        self.outcomes[test.id()]="expected"; super().addExpectedFailure(test,err)
    def addUnexpectedSuccess(self,test):
        self.outcomes[test.id()]="unexpected"; super().addUnexpectedSuccess(test)
    def addSubTest(self,test,subtest,err):
        if err is not None:
            self.outcomes[test.id()]="FAIL" if issubclass(err[0],test.failureException) else "ERROR"
        super().addSubTest(test,subtest,err)
suite=unittest.defaultTestLoader.discover("tests",top_level_dir=".")
result=unittest.TextTestRunner(stream=io.StringIO(),verbosity=0,failfast=True,resultclass=Result).run(suite)
print("__SKILL_GRADE_JSON__"+json.dumps({"outcomes":result.outcomes,"ok":result.wasSuccessful(),"n":result.testsRun}))
'''


def outcomes(root, env_extra=None, timeout=120):
    env={k:v for k,v in os.environ.items() if not k.startswith("SIM_")}
    env.update({"PYTHONPATH":str(root),**(env_extra or {})})
    try:
        p=subprocess.run([sys.executable,"-c",RUNNER],cwd=root,env=env,capture_output=True,text=True,timeout=timeout)
    except subprocess.TimeoutExpired:return {"__timeout__":True}
    lines=[x for x in p.stdout.splitlines() if x.startswith("__SKILL_GRADE_JSON__")]
    try:
        data=json.loads(lines[-1][len("__SKILL_GRADE_JSON__"):]);out=data["outcomes"]
        if p.returncode or not data["n"] or not out:return {"__crash__":p.stderr[-400:]}
        if not data["ok"] and all(v in ("ok","skipped","expected") for v in out.values()):return {"__crash__":"failure not represented"}
        return out
    except (ValueError,KeyError,IndexError,TypeError):return {"__crash__":p.stderr[-400:]}
