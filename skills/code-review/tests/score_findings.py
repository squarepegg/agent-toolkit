#!/usr/bin/env python3
"""Score a findings file against corpus/expected.json. Deterministic; use it to compare models or prompts.

  score_findings.py FINDINGS.json [--expected corpus/expected.json] [--min-recall 1.0]

A planted defect is found when a `confirmed` finding sits on one of its lines. A decoy is hit when a
`confirmed` finding sits on its lines (a false positive). Exit 0 if recall >= --min-recall and no decoy is hit.
"""
import argparse, json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))


def score(doc, exp):
    conf = [f for f in doc["findings"] if f["verdict"] == "confirmed"]
    hit = lambda item: any(f["file"] == item["file"] and f["line"] in item["lines"] for f in conf)
    found = sorted(p["id"] for p in exp["planted"] if hit(p))
    missed = sorted(p["id"] for p in exp["planted"] if not hit(p))
    fps = sorted(d["id"] for d in exp["decoys"] if hit(d))
    return {"found": found, "missed": missed, "decoys_hit": fps, "recall": len(found) / len(exp["planted"])}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("findings")
    ap.add_argument("--expected", default=os.path.join(HERE, "corpus", "expected.json"))
    ap.add_argument("--min-recall", type=float, default=1.0)
    a = ap.parse_args()
    res = score(json.load(open(a.findings)), json.load(open(a.expected)))
    print(json.dumps(res, sort_keys=True))
    return 0 if res["recall"] >= a.min_recall and not res["decoys_hit"] else 1


if __name__ == "__main__":
    sys.exit(main())
