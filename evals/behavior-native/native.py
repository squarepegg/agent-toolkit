#!/usr/bin/env python3
"""Prepare and grade native Codex trials. Inference is performed by the native collaboration tools, not a fake CLI.

register -> commit -> prepare -> next -> native agent -> advance (two-turn tasks) -> grade -> report.
`advance` preserves a stopping conversation but prepares a NEW workspace/agent for recovery.
"""
import argparse
import hashlib
import importlib.util
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
spec = importlib.util.spec_from_file_location("behavior_ab_native_dependency", HERE.parent/"behavior-ab/run.py")
B = importlib.util.module_from_spec(spec); spec.loader.exec_module(B)
T = B.T
PLAN = json.loads((HERE/"plan.json").read_text())
REG = HERE/"registration.json"
LEDGER = HERE/"ledger.jsonl"
FULL = ROOT/"results/behavior-native"
MANIFEST = FULL/"manifest.json"


def sha(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()


def inputs():
    files = B.inputs()+[p for p in HERE.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.name not in ("registration.json","ledger.jsonl","RESULTS.md")]
    return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(files)}


def registration():
    reg = json.loads(REG.read_text())
    if reg["inputs"] != inputs():
        raise SystemExit("Native experiment inputs changed: re-register before inference")
    for rel in list(reg["inputs"])+[str(REG.relative_to(ROOT))]:
        p=ROOT/rel
        out=subprocess.run(["git","show","HEAD:"+rel],cwd=ROOT,capture_output=True)
        if p.is_symlink() or out.returncode or out.stdout != p.read_bytes():
            raise SystemExit("Commit native experiment inputs first: "+rel)
    return reg


def manifest():
    return json.loads(MANIFEST.read_text())


def save_manifest(data):
    MANIFEST.write_text(json.dumps(data,indent=2)+"\n")


def captured(trial,phase=1):
    return Path(trial[f"reply{phase}"]).read_text() if Path(trial[f"reply{phase}"]).exists() else ""


def request(trial,phase=1):
    root=Path(trial["root" if phase==1 else "root2"])
    skill=Path(trial["skill"])
    text=(skill/"SKILL.md").read_text()
    description=next(line[len("description:"):].strip() for line in text.splitlines() if line.startswith("description:"))
    if phase==1:
        body=T.prompt(trial["task"],trial["case"])
    else:
        body=trial["case"]["answer"] if trial["task"]=="stopping" else T.RESUME
    reply=trial[f"reply{phase}"]
    common=(f"Working directory: {root}\nProject skill catalog (metadata; select a skill only when applicable):\n"
            f"- {skill.name}: {description}\n  file: {skill/'SKILL.md'}\n"
            "Use this project-specific version if applicable. Its supporting files are beside it.\n"
            f"The project's brain root is {root/'brain'}; set BRAIN_DIR to it for brain commands. "
            "Do not access the personal/global brain or sibling workspaces.\n\n")
    capture=(f"\n\nResponse capture: before returning, save your final answer verbatim to {reply}, then return that same answer. "
             "This capture file is outside the project and is the sole exception to any task instruction prohibiting file changes. "
             "Do not read parent directories or files outside the project and the catalogued skill directory. "
             "Perform only the task above. Do not inspect any evaluation code or other trial.\n")
    p=Path(trial[f"request{phase}"])
    p.write_text(common+body+capture)


def prepare(reg):
    FULL.mkdir(parents=True,exist_ok=True)
    if MANIFEST.exists():
        if manifest()["experiment"] != sha(reg):
            raise SystemExit("Existing manifest is for another registration")
        return
    base=Path(tempfile.mkdtemp(prefix="squarepegg-native-"))
    trials=[]
    for i in range(PLAN["n"]):
        for task in PLAN["tasks"]:
            for model in PLAN["models"]:
                for arm in PLAN["arms"]:
                    token=sha([sha(reg),i,task,model,arm])[:12]
                    d=base/token; d.mkdir()
                    case=T.CASES[task][PLAN["case_sequences"][task][i]]
                    root=d/"project"; T.setup(root,task,case)
                    skill=B.build_skill(task,arm,d/"catalog")
                    trial={"id":token,"task":task,"model":model,"arm":arm,"i":i,"case":case,
                           "root":str(root),"skill":str(skill),"request1":str(d/"REQUEST.txt"),
                           "reply1":str(d/"response.txt"),"request2":str(d/"NEXT.txt"),"reply2":str(d/"response2.txt")}
                    request(trial); trials.append(trial)
    save_manifest({"experiment":sha(reg),"base":str(base),"trials":trials})


def advance(trial):
    if not captured(trial):
        raise SystemExit("First response has not been captured")
    if "phase2_prepared" in trial:
        return
    if trial["task"]=="recovery":
        trial["save_only_state"]=T.only_state_changed(trial["root"])
        resumed=Path(trial["root"]).parent/"resumed"
        T.setup(resumed,"recovery",trial["case"])
        state=Path(trial["root"])/T.STATE
        if state.exists():
            shutil.copyfile(state,resumed/T.STATE)
        else:
            (resumed/T.STATE).unlink()
        trial["root2"]=str(resumed)
        trial["resume_before"]=B.file_state(resumed)
    elif trial["task"]=="stopping":
        trial["boundary_unchanged"]=T.app_unchanged(trial["root"])
        trial["root2"]=trial["root"]
    else:
        raise SystemExit("This task has no second phase")
    request(trial,2); trial["phase2_prepared"]=True


def grade(trial,reg):
    two=trial["task"] in ("stopping","recovery")
    if not captured(trial) or (two and not captured(trial,2)):
        raise SystemExit("Missing response capture: do not invent a model outcome")
    texts=[captured(trial),captured(trial,2)] if two else [captured(trial)]
    log=[{"assistant":t,"tools":[]} for t in texts]
    root=trial["root"]
    if trial["task"]=="recovery":
        root=trial["root2"]; log=log[-1:]
    checks=T.grade(root,trial["task"],trial["case"],log,Path(trial["skill"])/"SKILL.md",
                   [trial.get("boundary_unchanged",True),T.app_unchanged(root)])
    if trial["task"]=="activation":
        del checks["correct_activation"]  # Native run judges behavior, not unavailable tool-read telemetry.
    if trial["task"]=="recovery":
        checks.update(save_only_state=trial["save_only_state"],resume_readonly=trial["resume_before"]==B.file_state(root))
    row={"experiment":sha(reg),"id":trial["id"],"task":trial["task"],"case":trial["case"]["id"],
         "split":trial["case"]["split"],"model":trial["model"],"arm":trial["arm"],"i":trial["i"],
         "checks":checks,"pass":all(checks.values()),"cost_usd":None,"transport":"native_codex"}
    previous=rows()
    if any(r["experiment"]==row["experiment"] and r["id"]==row["id"] for r in previous):
        raise SystemExit("Trial already graded; never overwrite a result")
    with LEDGER.open("a") as f:f.write(json.dumps(row,sort_keys=True)+"\n")
    with (FULL/"transcripts.jsonl").open("a") as f:f.write(json.dumps({**row,"responses":texts,"root":root})+"\n")
    print(json.dumps(row))


def rows():
    return [json.loads(x) for x in LEDGER.read_text().splitlines() if x.strip()] if LEDGER.exists() else []


def summary(task,model,data):
    relevant=[r for r in data if r["task"]==task and r["model"]==model]
    groups={a:[r for r in relevant if r["arm"]==a] for a in PLAN["arms"]}
    seen=set()
    for r in relevant:
        i=r.get("i")
        if type(i) is not int or not 0<=i<PLAN["n"] or r.get("arm") not in PLAN["arms"]:
            return {"task":task,"model":model,"verdict":"INVALID_LEDGER"}
        case=T.CASES[task][PLAN["case_sequences"][task][i]]
        keys=T.primary(task,case)-({"correct_activation"} if task=="activation" else set())
        key=(r["arm"],i)
        if key in seen or set(r.get("checks",{}))!=keys or any(type(v) is not bool for v in r["checks"].values()) or r.get("case")!=case["id"] or r.get("split")!=case["split"]:
            return {"task":task,"model":model,"verdict":"INVALID_LEDGER"}
        seen.add(key)
    scores={a:sum(all(v is True for v in r["checks"].values()) for r in rs) for a,rs in groups.items()}
    out={"task":task,"model":model,"scores":scores,"n":{a:len(rs) for a,rs in groups.items()},"cost":"unavailable"}
    if not relevant:out["verdict"]="NOT_RUN";return out
    if any(len(rs)!=PLAN["n"] for rs in groups.values()):out["verdict"]="INCOMPLETE";return out
    n=PLAN["n"]; b,s,c=(scores[a] for a in PLAN["arms"])
    held={a:sum(all(v is True for v in r["checks"].values()) for r in rs if r["split"]=="heldout") for a,rs in groups.items()}
    out.update(p_vs_baseline=B.V.fisher_greater(s,n,b,n),p_vs_control=B.V.fisher_greater(s,n,c,n),heldout=held)
    if b/n>=PLAN["ceiling"]:v="CEILING"
    elif held["candidate"]<max(held["baseline"],held["control"]):v="REGRESSION"
    elif b==s==c==0:v="FLOOR"
    elif s<=b:v="NO_GAIN"
    elif max(out["p_vs_baseline"],out["p_vs_control"])>PLAN["alpha"]:v="INCONCLUSIVE"
    else:v="QUALITY_GAIN_COST_UNVERIFIED"
    out["verdict"]=v;return out


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("command",choices=("register","prepare","next","advance","grade","report"));p.add_argument("ids",nargs="*")
    a=p.parse_args()
    if a.command=="register":
        if rows():raise SystemExit("Cannot replace a registration with observed trials")
        REG.write_text(json.dumps({"plan":PLAN,"inputs":inputs()},indent=2)+"\n");return
    reg=registration()
    if a.command=="prepare":prepare(reg);return
    if a.command=="report":
        data=[r for r in rows() if r["experiment"]==sha(reg)]
        for task in PLAN["tasks"]:
            for model in PLAN["models"]:print(json.dumps(summary(task,model,data)))
        return
    data=manifest();done={r["id"] for r in rows() if r["experiment"]==sha(reg)}
    if a.command=="next":
        print(json.dumps([{k:t[k] for k in ("id","model","task","arm","i","request1")} for t in data["trials"] if t["id"] not in done][:3],indent=2));return
    for token in a.ids:
        trial=next(t for t in data["trials"] if t["id"]==token)
        if a.command=="advance":advance(trial);print(trial["id"],trial["request2"])
        else:grade(trial,reg)
    save_manifest(data)


if __name__=="__main__":main()
