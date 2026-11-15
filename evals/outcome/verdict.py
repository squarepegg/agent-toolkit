#!/usr/bin/env python3
"""Outcome verdicts from the committed ledger (free, deterministic, CI). The model runs happen on a laptop
(evals/outcome/run.py); this file only recomputes what the ledger rows say, so a verdict can't be typed in by hand.

  python3 evals/outcome/verdict.py            table of verdicts per task
  python3 evals/outcome/verdict.py --json     same, as JSON

A row counts only if its hashes match the current skill, control and task files (else it is STALE and ignored).
A run passes when it finished and every pre-registered primary check is true.

Verdicts per task (pre-registered `prereg` in the task file: n, alpha, ceiling, primary):
  NOT_RUN       no current rows
  INCOMPLETE    some arm has fewer than n current rows
  CEILING       the no-skill arm already passes >= ceiling: the task cannot show value; write a harder task
  WORTHWHILE    skill beats BOTH no-skill and control: one-sided Fisher exact p <= alpha against each
  NOT_WORTH     skill pass rate <= no-skill pass rate
  INCONCLUSIVE  anything else (a gain, but not significant at this n, or the control arm does as well)
"""
import glob
import hashlib
import json
import os
import sys
from math import comb

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
import registry as T  # noqa: E402

ARMS = ("none", "skill", "control")
LEDGER = os.path.join(HERE, "ledger")
CONTROL = os.path.join(HERE, "control.md")


def _read(p):
    with open(p, "rb") as f:
        return f.read()


def _sha(items):
    h = hashlib.sha256()
    for rel, data in items:
        h.update(rel.encode() + b"\0" + data + b"\0")
    return h.hexdigest()[:16]


def skill_files(skill):
    base = os.path.join(ROOT, "skills", skill)
    out = []
    for p in sorted(glob.glob(os.path.join(base, "**", "*"), recursive=True)):
        rel = os.path.relpath(p, base)
        if os.path.isdir(p) or rel.split(os.sep)[0] in ("evals", "tests") or "__pycache__" in rel:
            continue
        out.append((rel, p))
    return out


def skill_hash(skill):
    return _sha([(rel, _read(p)) for rel, p in skill_files(skill)])


def control_hash(skill):
    return _sha([("control.md", _read(CONTROL)), ("SKILL.md", _read(os.path.join(ROOT, "skills", skill, "SKILL.md")))])


def task_hash(task_id):
    t = T.TASKS[task_id]
    return _sha([(os.path.relpath(f, ROOT), _read(f)) for f in t["files"]] + [("prereg", json.dumps(t["prereg"], sort_keys=True).encode())])


def current_hashes(task_id):
    s = T.TASKS[task_id]["skill"]
    return {"skill": skill_hash(s), "control": control_hash(s), "task": task_hash(task_id)}


def fisher_greater(a, n1, c, n2):
    """One-sided Fisher exact p-value that arm 1 (a of n1 passes) beats arm 2 (c of n2)."""
    k, n = a + c, n1 + n2
    if k == 0:
        return 1.0
    total = comb(n, k)
    return sum(comb(n1, x) * comb(n2, k - x) for x in range(a, min(n1, k) + 1)) / total


def load_rows(task_id):
    p = os.path.join(LEDGER, f"{task_id}.jsonl")
    if not os.path.exists(p):
        return []
    return [json.loads(line) for line in _read(p).decode().splitlines() if line.strip()]


def passed(row, primary):
    return bool(row.get("finished")) and all(row["checks"].get(c) is True for c in primary)


def verdict(task_id, rows=None):
    t = T.TASKS[task_id]
    pre = t["prereg"]
    rows = load_rows(task_id) if rows is None else rows
    cur = current_hashes(task_id)
    fresh = [r for r in rows if r.get("hashes") == cur]
    by = {a: [r for r in fresh if r["arm"] == a] for a in ARMS}
    score = {a: (sum(passed(r, pre["primary"]) for r in by[a]), len(by[a])) for a in ARMS}
    out = {"task": task_id, "skill": t["skill"], "score": score, "stale_rows": len(rows) - len(fresh), "n": pre["n"],
           "cost_usd": round(sum(float(r.get("cost_usd") or 0) for r in fresh), 2),
           "models": sorted({r.get("model", "?") for r in fresh})}
    if not fresh:
        out["verdict"] = "NOT_RUN"
        return out
    if any(score[a][1] < pre["n"] for a in ARMS):
        out["verdict"] = "INCOMPLETE"
        return out
    (s, ns), (b, nb), (c, nc) = score["skill"], score["none"], score["control"]
    out["p_vs_none"] = round(fisher_greater(s, ns, b, nb), 4)
    out["p_vs_control"] = round(fisher_greater(s, ns, c, nc), 4)
    if b / nb >= pre["ceiling"]:
        out["verdict"] = "CEILING"
    elif out["p_vs_none"] <= pre["alpha"] and out["p_vs_control"] <= pre["alpha"]:
        out["verdict"] = "WORTHWHILE"
    elif s / ns <= b / nb:
        out["verdict"] = "NOT_WORTH"
    else:
        out["verdict"] = "INCONCLUSIVE"
    return out


def validate_ledger():
    """-> list of problems: a ledger row that could not have been written by run.py"""
    probs = []
    for p in sorted(glob.glob(os.path.join(LEDGER, "*.jsonl"))):
        tid = os.path.basename(p)[:-6]
        if tid not in T.TASKS:
            probs.append(f"{p}: no task {tid!r}")
            continue
        seen = set()
        for n, line in enumerate(_read(p).decode().splitlines(), 1):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                probs.append(f"{p}:{n}: not JSON")
                continue
            need = {"task", "arm", "i", "finished", "checks", "hashes", "model", "git_head", "date"}
            if need - set(r):
                probs.append(f"{p}:{n}: missing {sorted(need - set(r))}")
                continue
            if r["task"] != tid or r["arm"] not in ARMS:
                probs.append(f"{p}:{n}: wrong task or arm")
            if "--model" not in r["model"]:
                probs.append(f"{p}:{n}: model not pinned")
            key = (r["arm"], r["i"], json.dumps(r["hashes"], sort_keys=True))
            if key in seen:
                probs.append(f"{p}:{n}: duplicate run {key[:2]}")
            seen.add(key)
            missing = [c for c in T.TASKS[tid]["prereg"]["primary"] if c not in r["checks"]]
            if missing:
                probs.append(f"{p}:{n}: primary checks not graded: {missing}")
    return probs


def main(argv):
    res = [verdict(t) for t in T.TASKS]
    probs = validate_ledger()
    if "--json" in argv:
        print(json.dumps({"verdicts": res, "problems": probs}, indent=1))
    else:
        for v in res:
            sc = " ".join(f"{a} {v['score'][a][0]}/{v['score'][a][1]}" for a in ARMS)
            p = f" p(vs none)={v.get('p_vs_none')} p(vs control)={v.get('p_vs_control')}" if "p_vs_none" in v else ""
            print(f"{v['task']:18} {v['verdict']:12} {sc}{p} stale={v['stale_rows']} ${v['cost_usd']}")
        for x in probs:
            print("LEDGER PROBLEM " + x)
    return 1 if probs else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
