#!/usr/bin/env python3
"""Post-hoc diagnostic sensitivity check, NOT a replacement for the registered recovery score."""
import json
from pathlib import Path

HERE=Path(__file__).resolve().parent


def unrun_preserved(value):
    return (isinstance(value,list) and len(value)==1 and isinstance(value[0],str) and
            " ".join(value[0].casefold().split()) in ("browser smoke","browser smoke test"))


def summarize(records):
    passed=0
    for record in records:
        other=all(v is True for k,v in record["checks"].items() if k!="unrun_evidence")
        passed+=bool(other and unrun_preserved(record["response"].get("unrun_checks")))
    return {"rows":len(records),"registered_passes":sum(all(v is True for v in r["checks"].values()) for r in records),
            "passes_with_equivalent_check_names":passed,"diagnostic_only":True}


if __name__=="__main__":
    print(json.dumps(summarize(json.loads((HERE/"handoff-responses.json").read_text())),indent=2))
