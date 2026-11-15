#!/usr/bin/env python3
"""Post-hoc conservative stopping analysis. Never changes the registered experiment or its INCOMPLETE verdicts.

Allow every remaining candidate observation to pass and every remaining comparator observation to fail.
If even that cannot satisfy the PRE-REGISTERED significance rule, completing n cannot justify adoption.
This can only rule out adoption, never manufacture a positive result. No unrun observation is entered in the ledger.
"""
import importlib.util
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("native_futility_dependency",HERE.parent/"behavior-native/native.py")
N=importlib.util.module_from_spec(spec);spec.loader.exec_module(N)


def best_case(candidate_passes,candidate_seen,baseline_passes,control_passes,n,alpha):
    if not 0<=candidate_passes<=candidate_seen<=n or not 0<=baseline_passes<=n or not 0<=control_passes<=n:
        raise ValueError("Invalid observed counts")
    upper=candidate_passes+n-candidate_seen
    pb=N.B.V.fisher_greater(upper,n,baseline_passes,n)
    pc=N.B.V.fisher_greater(upper,n,control_passes,n)
    return {"candidate_max_final_passes":upper,"best_p_vs_baseline":pb,"best_p_vs_control":pc,
            "could_meet_significance_rule":max(pb,pc)<=alpha}


def analyze(reg,data):
    fresh=[r for r in data if r["experiment"]==N.sha(reg)]
    if any(r["task"] not in N.PLAN["tasks"] or r["model"] not in N.PLAN["models"] for r in fresh):
        raise ValueError("Unknown task or model")
    results=[]
    for task in N.PLAN["tasks"]:
        for model in N.PLAN["models"]:
            checked=N.summary(task,model,fresh)
            if checked["verdict"]=="INVALID_LEDGER":raise ValueError("Invalid ledger")
            groups={a:[r for r in fresh if r["task"]==task and r["model"]==model and r["arm"]==a] for a in N.PLAN["arms"]}
            wins={a:sum(all(v is True for v in r["checks"].values()) for r in rs) for a,rs in groups.items()}
            result={"task":task,"model":model,"observed":{a:f"{wins[a]}/{len(rs)}" for a,rs in groups.items()},
                    **best_case(wins["candidate"],len(groups["candidate"]),wins["baseline"],wins["control"],N.PLAN["n"],N.PLAN["alpha"])}
            results.append(result)
    return {"observations":len(fresh),"planned_observations":len(N.PLAN["tasks"])*len(N.PLAN["models"])*len(N.PLAN["arms"])*N.PLAN["n"],
            "all_candidates_futile":all(not r["could_meet_significance_rule"] for r in results),"cells":results}


if __name__=="__main__":
    print(json.dumps(analyze(N.registration(),N.rows()),indent=2))
