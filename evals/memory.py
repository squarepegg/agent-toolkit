#!/usr/bin/env python3
"""Self-improvement memory for skill candidates (stdlib only). `evals/memory/attempts.jsonl` is an append-only ledger of every
skill/harness edit that was tried: the recurring problem it addressed, the rationale, the predicted and the observed outcome, the
model it was evaluated on. The gate and `run_candidate.py` read it before spending tokens.

  python3 evals/memory.py --check      validate the ledger and that evals/memory/REPORT.md is current
  python3 evals/memory.py --report     rewrite REPORT.md (backtest + calibration)
  python3 evals/memory.py history SKILL PROBLEM   prior attempts for one recurring problem

Rule (the re-architect trigger of skills/evidence-gated): a new attempt on the same (skill, problem, model) after REPEAT_AFTER
informative failures is a REPEAT unless it states `retry_because`. Informative failures are verdicts that say something about the
skill (CEILING, FLOOR, NO_DELTA, REGRESSION, REJECT, TOO_COSTLY, NOT_WORTH). UNSOUND_*, CONFOUNDED, INVALID_MODEL, INCONCLUSIVE and REPEAT
say something about the experiment, so they do not count. An ADOPT closes the corner: failures before it are forgotten.
A different model is new evidence, which is why it does not match. An unknown model (None) matches any."""
import json, os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
LEDGER = os.path.join(HERE, "memory", "attempts.jsonl")
REPORT = os.path.join(HERE, "memory", "REPORT.md")
REPEAT_AFTER = 2
INFORMATIVE_FAIL = {"CEILING", "FLOOR", "NO_DELTA", "REGRESSION", "REJECT", "TOO_COSTLY", "NOT_WORTH"}
NEUTRAL = {"UNSOUND_GRADER", "UNSOUND_REFERENCE", "CONFOUNDED", "INVALID_MODEL", "INCONCLUSIVE", "REPEAT"}
VERDICTS = INFORMATIVE_FAIL | NEUTRAL | {"ADOPT"}
REQUIRED = ("id", "date", "skill", "problem", "change", "rationale", "observed", "verdict")
KEYS = set(REQUIRED) | {"predicted", "model", "matched", "cost_usd", "retry_because"}
MATCHED = {None, "yes", "partly", "no"}


class BadLedger(Exception):
    pass


def validate(rows):
    seen, last = set(), ""
    for i, r in enumerate(rows, 1):
        where = f"attempts.jsonl row {i}"
        if not isinstance(r, dict):
            raise BadLedger(f"{where}: not an object")
        if set(r) - KEYS:
            raise BadLedger(f"{where}: unknown keys {sorted(set(r) - KEYS)}")
        for k in REQUIRED:
            if not isinstance(r.get(k), str) or not r[k].strip():
                raise BadLedger(f"{where}: `{k}` must be a non-empty string")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["date"]):
            raise BadLedger(f"{where}: date must be YYYY-MM-DD")
        if r["id"] in seen:
            raise BadLedger(f"{where}: duplicate id {r['id']}")
        seen.add(r["id"])
        if r["date"] < last:
            raise BadLedger(f"{where}: dates must not go backwards (append-only)")
        last = r["date"]
        if r["verdict"] not in VERDICTS:
            raise BadLedger(f"{where}: verdict {r['verdict']!r} not in {sorted(VERDICTS)}")
        if r.get("matched") not in MATCHED:
            raise BadLedger(f"{where}: matched must be yes|partly|no|null")
        if r.get("matched") and not r.get("predicted"):
            raise BadLedger(f"{where}: `matched` without a `predicted`")
        for k in ("predicted", "model", "retry_because"):
            if r.get(k) is not None and (not isinstance(r[k], str) or not r[k].strip()):
                raise BadLedger(f"{where}: `{k}` must be null or a non-empty string")
        c = r.get("cost_usd")
        if c is not None and (isinstance(c, bool) or not isinstance(c, (int, float)) or c < 0):
            raise BadLedger(f"{where}: cost_usd must be null or a non-negative number")
    return rows


def load(path=LEDGER):
    with open(path) as fh:
        rows = []
        for n, ln in enumerate(fh, 1):
            if ln.strip():
                try:
                    rows.append(json.loads(ln))
                except ValueError as e:
                    raise BadLedger(f"attempts.jsonl line {n}: {e}")
    return validate(rows)


def same_model(a, b):
    return a is None or b is None or a == b


def prior(rows, skill, problem, model):
    """Attempts on this corner (same model or unknown), oldest first, ending at the newest ADOPT if any."""
    out = [r for r in rows if r["skill"] == skill and r["problem"] == problem and same_model(r.get("model"), model)]
    for i in range(len(out) - 1, -1, -1):
        if out[i]["verdict"] == "ADOPT":
            return out[i + 1:]
    return out


def repeat_of(rows, skill, problem, model, retry_because=None):
    """-> the informative failures that make a new attempt a REPEAT, or [] (allowed)."""
    if retry_because and str(retry_because).strip():
        return []
    fails = [r for r in prior(rows, skill, problem, model) if r["verdict"] in INFORMATIVE_FAIL]
    return fails if len(fails) >= REPEAT_AFTER else []


def render_history(rows):
    return "\n".join(f"  {r['date']} {r['id']} [{r['verdict']}] {r['change']} -> {r['observed']}" for r in rows) or "  (none)"


def ledger_line(c, res, verdict, f, model, date):
    """The ledger row a finished gate run would append (printed by gate.py; a human reviews and appends it)."""
    row = {"id": c["id"], "date": date, "skill": c["skill"], "problem": c.get("problem") or "UNSET", "change": c["claim"],
           "rationale": c["source"], "predicted": c.get("predicted") or f"dev gain >= {c.get('min_gain', 2)}", "observed":
           f"dev {f['dev_without']}/{f['dev_n']} -> {f['dev_with']}/{f['dev_n']}, heldout {f['heldout_without']} -> {f['heldout_with']}, cost x{f['cost_ratio']}",
           "verdict": verdict, "model": model, "matched": "yes" if verdict == "ADOPT" else "no", "cost_usd": round(f["cost_without"] + f["cost_with"], 4),
           "retry_because": c.get("retry_because")}
    return json.dumps(row, sort_keys=True)


def backtest(rows):
    """Replay the ledger oldest first. For each attempt, would the trigger have fired using only earlier rows?"""
    fired = []
    for i, r in enumerate(rows):
        fails = [p for p in prior(rows[:i], r["skill"], r["problem"], r.get("model")) if p["verdict"] in INFORMATIVE_FAIL]
        if len(fails) >= REPEAT_AFTER:
            fired.append({"id": r["id"], "priors": [p["id"] for p in fails], "justified": bool(r.get("retry_because")),
                          "verdict": r["verdict"], "cost_usd": r.get("cost_usd")})
    return fired


def calibration(rows):
    pred = [r for r in rows if r.get("predicted")]
    return {"n": len(pred), **{m: sum(1 for r in pred if r.get("matched") == m) for m in ("yes", "partly", "no")},
            "unjudged": sum(1 for r in pred if not r.get("matched"))}


def report(rows):
    fired, cal = backtest(rows), calibration(rows)
    by = {}
    for r in rows:
        by[r["verdict"]] = by.get(r["verdict"], 0) + 1
    L = ["# Memory report (generated by `python3 evals/memory.py --report`; CI checks it is current)", "",
         f"Attempts: {len(rows)}; verdicts: " + ", ".join(f"{k} {v}" for k, v in sorted(by.items())), "",
         f"## Backtest: re-architect trigger (>= {REPEAT_AFTER} informative failures on the same skill, problem and model)", "",
         f"Fired on {len(fired)} of {len(rows)} attempts; {sum(1 for x in fired if x['justified'])} carried a `retry_because`, "
         f"{sum(1 for x in fired if not x['justified'])} would have been blocked.", ""]
    L += ["| attempt | prior failures | justified | verdict |", "|---|---|---|---|"]
    L += [f"| {x['id']} | {', '.join(x['priors'])} | {'yes' if x['justified'] else 'NO'} | {x['verdict']} |" for x in fired] or ["| (none) | | | |"]
    L += ["", f"After the trigger fired: {sum(1 for x in fired if x['verdict'] in INFORMATIVE_FAIL)} of {len(fired)} attempts were informative failures again.",
          "Caveat: `retry_because` on seeded rows was reconstructed from the free-text log, and n is tiny. This shows the mechanism is exercised by real history; it does not show the gate saves money.", "",
          "## Calibration (predictions written before the run vs observed)", "",
          f"{cal['n']} of {len(rows)} attempts recorded a prediction: matched yes {cal['yes']}, partly {cal['partly']}, no {cal['no']}, unjudged {cal['unjudged']}.",
          "Most seeded rows predate the `predicted` field; new candidates should fill it in before the run.", ""]
    return "\n".join(L)


def main(argv):
    try:
        rows = load()
    except (BadLedger, OSError) as e:
        print(f"memory: {e}", file=sys.stderr)
        return 2
    if argv[:1] == ["history"] and len(argv) == 3:
        print(render_history(prior(rows, argv[1], argv[2], None)))
        return 0
    if "--report" in argv:
        with open(REPORT, "w") as fh:
            fh.write(report(rows))
        return 0
    if "--check" in argv:
        try:
            ok = open(REPORT).read() == report(rows)
        except OSError:
            ok = False
        if not ok:
            print("memory: REPORT.md is stale: python3 evals/memory.py --report", file=sys.stderr)
            return 1
        print(f"ok: {len(rows)} attempts")
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
