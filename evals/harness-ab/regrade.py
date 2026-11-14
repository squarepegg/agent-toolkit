#!/usr/bin/env python3
"""Re-grade finished runs with the current graders (their workdirs are kept), so a grader fix applies to every arm alike.
  python3 evals/harness-ab/regrade.py <in.jsonl> <out.jsonl>"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tasks as T  # noqa: E402


def regrade(x):
    r = os.path.join(x["workdir"], "repo"); log = x["log"]
    final = log[-1]["assistant"] if log else ""
    all_text = "\n".join(t["assistant"] for t in log)
    bash = [json.loads(c["args"]).get("command", "") if c["args"].endswith("}") else c["args"] for t in log for c in t["tools"] if c["name"] == "bash"]
    checks, detail = T.dst_grade(r, final, all_text) if x["task"] == "dst" else T.doubt_grade(r, final, all_text, bash)
    x.update(checks=checks, detail=detail, passed=sum(checks.values()), n_checks=len(checks)); return x


if __name__ == "__main__":
    rows = [regrade(json.loads(line)) for line in open(sys.argv[1])]
    with open(sys.argv[2], "w") as f:
        for x in rows: f.write(json.dumps(x) + "\n")
    for x in rows: print(f"{x['task']}-{x['arm']} #{x['i']}: {x['passed']}/{x['n_checks']} fails={[k for k, v in x['checks'].items() if not v]}")
