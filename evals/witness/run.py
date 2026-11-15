#!/usr/bin/env python3
"""Run every advice witness (free, deterministic, CI). Exit 1 if any arm does not behave as its claim says.

  python3 evals/witness/run.py              all witnesses, one line per arm
  python3 evals/witness/run.py --only ID    one witness (repeatable: --only a --only b)
  python3 evals/witness/run.py --list       ids and what each shows
  python3 evals/witness/run.py --json FILE  also write the results as JSON (evals/check_claims.py reads ids from the registry)
"""
import json
import sys

import core


def main(argv):
    reg = core.load_all()
    if "--list" in argv:
        for w in reg.values():
            print(f"{w.id:34} {w.skill:18} {w.shows}")
        return 0
    only = [argv[i + 1] for i, a in enumerate(argv) if a == "--only"]
    unknown = set(only) - set(reg)
    if unknown:
        print(f"unknown witness ids: {sorted(unknown)}", file=sys.stderr)
        return 2
    bad, rows = 0, []
    for w in reg.values():
        if only and w.id not in only:
            continue
        for r in core.run_witness(w):
            want = "catch" if r.expect_caught else "miss "
            got = "CRASH" if r.caught is None else ("catch" if r.caught else "miss ")
            mark = "ok  " if r.ok else "FAIL"
            bad += not r.ok
            print(f"{mark} {w.id:30} {r.name:44} want {want} got {got} {r.seconds:5.2f}s  {r.detail[:110]}", flush=True)
            rows.append({"witness": w.id, "skill": w.skill, "arm": r.name, "expect_caught": r.expect_caught,
                         "caught": r.caught, "ok": r.ok, "detail": r.detail})
    if "--json" in argv:
        with open(argv[argv.index("--json") + 1], "w") as f:
            json.dump(rows, f, indent=1)
    n_w = len({r["witness"] for r in rows})
    print(f"\n{n_w} witnesses, {len(rows)} arms, {bad} failing")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
