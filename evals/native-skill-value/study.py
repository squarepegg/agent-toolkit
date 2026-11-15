#!/usr/bin/env python3
"""Native adapter for the EXISTING outcome fixtures, graders, generic control and permutation test.

register -> commit -> prepare -> next -> dispatch ID -> native agent -> grade ID -> report.
No inference in this script or CI. Completed observations are append-only and model-specific.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE=Path(__file__).resolve().parent;ROOT=HERE.parent.parent
sys.path.insert(0,str(HERE.parent/"outcome"))
import registry as R
import verdict as V
PLAN=json.loads((HERE/"plan.json").read_text())
REG=HERE/"registration.json";LEDGER=HERE/"ledger.jsonl";FULL=ROOT/"results/native-skill-value";MANIFEST=FULL/"manifest.json"


def digest(obj):return hashlib.sha256(json.dumps(obj,sort_keys=True).encode()).hexdigest()


def inputs():
    files=set()
    for base in [HERE,HERE.parent/"outcome",HERE.parent/"harness-ab"]:
        files.update(p for p in base.rglob("*") if p.is_file() and "__pycache__" not in p.parts and
                     p.name not in ("registration.json","ledger.jsonl","RESULTS.md") and "ledger" not in p.relative_to(base).parts)
    for task in PLAN["tasks"]:files.update(Path(p) for _,p in V.skill_files(R.TASKS[task]["skill"]))
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def registration():
    reg=json.loads(REG.read_text())
    if reg["inputs"]!=inputs():raise SystemExit("Registered inputs changed")
    for rel in list(reg["inputs"])+[str(REG.relative_to(ROOT))]:
        p=ROOT/rel;out=subprocess.run(["git","show","HEAD:"+rel],cwd=ROOT,capture_output=True)
        if p.is_symlink() or out.returncode or out.stdout!=p.read_bytes():raise SystemExit("Commit inputs first: "+rel)
    return reg


def rows():return [json.loads(x) for x in LEDGER.read_text().splitlines() if x.strip()] if LEDGER.exists() else []


def arm_text(task,arm):
    if arm=="none":return None
    text=(ROOT/"skills"/R.TASKS[task]["skill"]/"SKILL.md").read_text()
    if arm=="skill":return text
    end=text.index("\n---",4)+4;generic=(HERE.parent/"outcome/control.md").read_text()
    body="\n"+generic
    return text[:end]+(body*(1+(len(text)-end)//len(body)))[:len(text)-end]


def setup(trial):
    root=Path(trial["root"]);root.mkdir(parents=True)
    task=R.TASKS[trial["task"]];task["setup"](str(root))
    catalog=Path(trial["base"])/"catalog";text=arm_text(trial["task"],trial["arm"])
    if text is not None:
        skill=catalog/task["skill"];skill.mkdir(parents=True)
        if trial["arm"]=="skill":
            for rel,p in V.skill_files(task["skill"]):
                dest=skill/rel;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(p,dest)
        (skill/"SKILL.md").write_text(text)
        instruction=f"For this trial, use only the project skill at {skill/'SKILL.md'}. Read it before doing the task. Its supporting files are beside it.\n"
    else:instruction="No skill instructions are supplied for this trial. Complete the task without reading any SKILL.md or external skill resources.\n"
    original=task.get("prompt_none") or task["prompt"]
    request=(f"Controlled task. Work ONLY in {root}. {instruction}"
             "Do not read ambient/global skills, personal/company brains, other trials, evaluation code or parent workspaces. "
             "Use the project files, Python stdlib, and the provided project skill copy if any. Do not delegate or use external model CLIs. "
             "No user-input tools are connected; finish using routine judgment. "
             "Tests must check observable behavior, not read/hash implementation source or detect a grading environment.\n\n"+original+
             f"\n\nBefore returning, save your final answer verbatim to {trial['reply']} and return the same answer. "
             f"Also save JSON to {trial['exposure']} with a skill_files_read array listing ALL skill paths you actually read "
             "(empty if none). These two capture files are outside the project and are allowed. Do not invent tool telemetry.\n")
    Path(trial["request"]).write_text(request)


def prepare(reg):
    FULL.mkdir(parents=True,exist_ok=True)
    if MANIFEST.exists():return
    base=Path(tempfile.mkdtemp(prefix="squarepegg-value-"));trials=[]
    for i in range(PLAN["full_n"]):
        for task in PLAN["tasks"]:
            for model in PLAN["models"]:
                for arm in PLAN["arms"]:
                    token=digest([digest(reg),task,model,arm,i])[:12];d=base/token;d.mkdir()
                    t=dict(id=token,task=task,model=model,arm=arm,i=i,base=str(d),root=str(d/"project"),
                           request=str(d/"REQUEST.txt"),reply=str(d/"response.txt"),exposure=str(d/"exposure.json"))
                    setup(t);trials.append(t)
    MANIFEST.write_text(json.dumps(dict(experiment=digest(reg),base=str(base),trials=trials),indent=2)+"\n")


def clean_outcomes(out):
    return bool(out) and not any(k.startswith("__") for k in out) and all(v in ("ok","skipped","expected") for v in out.values())


def grade_artifact(task,root,text):
    t=R.TASKS[task];checks,detail=t["grade"](str(root),text,text,[])
    if task=="property-tests":
        module=sys.modules["task_property_tests"];again=module._outcomes_with(str(root),module.REAL)
        for name in module.MUT_NAMES:
            if any(k.startswith("__") for k in detail[name]):checks["kills_"+name]=False
        first=detail["real"]
    else:
        module=sys.modules["task_correctness_first"];again=module._with(str(root),module.REAL)
        # The existing grader rejects crash/timeout kills. Re-run real code separately for replay.
        first=module._with(str(root),module.REAL)
    checks["real_replays"]=first==again and clean_outcomes(again)
    kills=[k for k in checks if k.startswith("kills_")]
    gates=["passes_real","src_unchanged","real_replays"]
    score=sum(checks[k] is True for k in kills) if all(checks[g] is True for g in gates) else 0
    return checks,detail,score


def expected_keys(task):
    keys=set(R.TASKS[task]["prereg"]["primary"])|{"real_replays"}
    keys|={"names_dumb_implementation"} if task=="property-tests" else {"names_workload","names_gray_failure"}
    return keys


def score(row):
    c=row["checks"]
    return sum(v is True for k,v in c.items() if k.startswith("kills_")) if all(c.get(k) is True for k in ("passes_real","src_unchanged","real_replays")) else 0


def cell(task,model,data):
    rs=[r for r in data if r["task"]==task and r["model"]==model];groups={a:[r for r in rs if r["arm"]==a] for a in PLAN["arms"]}
    seen=set()
    for r in rs:
        key=(r["arm"],r["i"])
        if key in seen or r["arm"] not in PLAN["arms"] or type(r["i"]) is not int or not 0<=r["i"]<PLAN["full_n"] or set(r["checks"])!=expected_keys(task) or any(type(v) is not bool for v in r["checks"].values()) or r.get("score")!=score(r):
            return dict(task=task,model=model,verdict="INVALID_LEDGER")
        seen.add(key)
    scores={a:[score(r) for r in sorted(gr,key=lambda r:r["i"])] for a,gr in groups.items()};mx=PLAN["test_faults"][task]
    out=dict(task=task,model=model,scores=scores,max_score=mx,verdict="NOT_RUN",means={a:sum(x)/len(x) if x else None for a,x in scores.items()})
    if not rs:return out
    if any(r.get("exposure_status")!="in_scope_self_reported" for r in rs):out["verdict"]="EXPOSURE_UNVERIFIED";return out
    if all(len(scores[a])>=PLAN["pilot_n"] for a in PLAN["arms"]) and all(x==mx for x in scores["none"][:PLAN["pilot_n"]]):
        out["verdict"]="CEILING";return out
    if any(len(scores[a])<PLAN["full_n"] for a in PLAN["arms"]):out["verdict"]="INCOMPLETE";return out
    ps={a:V.perm_p_greater(scores["skill"],scores[a]) for a in ("none","control")};out["p_values"]=ps
    gain=all(out["means"]["skill"]-out["means"][a]>=PLAN["min_gain_mutants"] for a in ps)
    regression=any(sum(r["checks"][k] for r in groups["skill"])<max(sum(r["checks"][k] for r in groups[a]) for a in ps) for k in ("passes_real","src_unchanged","real_replays"))
    if regression:v="REGRESSION"
    elif all(x==0 for vals in scores.values() for x in vals):v="FLOOR"
    elif out["means"]["skill"]<=out["means"]["none"]:v="NO_MEASURED_GAIN"
    elif gain and all(p<=PLAN["alpha"] for p in ps.values()):v="QUALITY_GAIN_COST_UNKNOWN"
    else:v="INCONCLUSIVE"
    out["verdict"]=v;return out


def collect(t,reg):
    if not Path(t["reply"]).is_file():raise SystemExit("No completed response: not a model observation")
    if any(r["id"]==t["id"] and r["experiment"]==digest(reg) for r in rows()):raise SystemExit("Already graded")
    text=Path(t["reply"]).read_text();checks,detail,result=grade_artifact(t["task"],Path(t["root"]),text)
    exposure=None
    try:exposure=json.loads(Path(t["exposure"]).read_text())["skill_files_read"]
    except (OSError,ValueError,KeyError):pass
    allowed=Path(t["base"])/"catalog"
    valid=isinstance(exposure,list) and all(isinstance(x,str) and Path(x).is_absolute() and Path(x).resolve().is_relative_to(allowed.resolve()) for x in exposure)
    valid=valid and ((not exposure) if t["arm"]=="none" else bool(exposure))
    test_bytes=sum(p.stat().st_size for p in (Path(t["root"])/"tests").rglob("*.py"))
    row={k:t[k] for k in ("id","task","model","arm","i")}
    row.update(experiment=digest(reg),checks=checks,score=result,exposure_status="in_scope_self_reported" if valid else "unverified_or_contaminated",test_bytes=test_bytes,
               observed_wall_s=round(max(0,Path(t["reply"]).stat().st_mtime-t.get("dispatched_at",Path(t["reply"]).stat().st_mtime)),2),tokens=None,cost_usd=None)
    with LEDGER.open("a") as f:f.write(json.dumps(row,sort_keys=True)+"\n")
    with (FULL/"transcripts.jsonl").open("a") as f:f.write(json.dumps(dict(**row,detail=detail,response=text,exposure=exposure,root=t["root"]))+"\n")
    print(json.dumps(row))


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("command",choices=("register","prepare","next","dispatch","grade","report"));p.add_argument("ids",nargs="*");a=p.parse_args()
    if a.command=="register":
        if rows():raise SystemExit("Cannot replace an observed registration")
        REG.write_text(json.dumps(dict(plan=PLAN,inputs=inputs()),indent=2)+"\n");return
    reg=registration();data=[r for r in rows() if r["experiment"]==digest(reg)]
    if a.command=="report":
        invalid=False
        for task in PLAN["tasks"]:
            for model in PLAN["models"]:
                out=cell(task,model,data);print(json.dumps(out));invalid|=out["verdict"]=="INVALID_LEDGER"
        raise SystemExit(int(invalid))
    if a.command=="prepare":prepare(reg);return
    m=json.loads(MANIFEST.read_text());assert m["experiment"]==digest(reg)
    if a.command=="next":
        done={r["id"] for r in data};stopped={(t,mo) for t in PLAN["tasks"] for mo in PLAN["models"] if cell(t,mo,data)["verdict"] not in ("NOT_RUN","INCOMPLETE")}
        print(json.dumps([{k:t[k] for k in ("id","task","model","arm","i")} for t in m["trials"] if t["id"] not in done and (t["task"],t["model"]) not in stopped][:3],indent=2));return
    for token in a.ids:
        trial=next(t for t in m["trials"] if t["id"]==token)
        if a.command=="dispatch":trial["dispatched_at"]=time.time();print(token,trial["request"])
        else:collect(trial,reg)
    MANIFEST.write_text(json.dumps(m,indent=2)+"\n")


if __name__=="__main__":main()
