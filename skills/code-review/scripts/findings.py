#!/usr/bin/env python3
"""Validate, merge and render review findings. Deterministic; stdlib only.

  findings.py validate F.json
  findings.py merge A.json B.json [...] -o OUT.json     union of independent finder runs
  findings.py report F.json [--tier T0..T3]             exit 1 if invalid or the tier's finder passes were not met

File shape:
  {"runs": 1, "reviewed": ["..."], "not_reviewed": ["..."],
   "findings": [{"id","axis","severity","file","line","claim","evidence":{"kind","ref"},
                 "verdict","class"?,"check"?,"reason"?,"fix"?}]}

Rules (each is a way a review goes wrong, so each is enforced):
  - no evidence, no finding: evidence.kind in code|test|command and a non-empty ref
  - needs_validation must say which check settles it; rejected must say why
  - not_reviewed must be present (may be empty only if stated explicitly as [])
  - the tier's minimum finder passes (T3 = 2) must have been run before a report is accepted
Exit codes: 0 ok, 1 invalid or gate failed, 2 usage/IO.
"""
import argparse, json, re, sys

TOP = {"runs", "reviewed", "not_reviewed", "findings"}
KEYS = {"id", "axis", "severity", "file", "line", "claim", "evidence", "verdict", "class", "check", "reason", "fix"}
REQ = ("id", "axis", "severity", "file", "line", "claim", "evidence", "verdict")
AXES = ("standards", "spec")
SEV = ("blocker", "major", "minor", "nit")
VERDICTS = ("confirmed", "needs_validation", "rejected")
KINDS = ("code", "test", "command")
MIN_PASSES = {"T0": 1, "T1": 1, "T2": 1, "T3": 2}


def load(path):
    try:
        return json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"findings: cannot read {path}: {e}", file=sys.stderr)
        sys.exit(2)


def nonempty(v):
    return isinstance(v, str) and v.strip() != ""


def validate(doc):
    errs = []
    if not isinstance(doc, dict):
        return ["top level must be an object"]
    for k in sorted(set(doc) - TOP):
        errs.append(f"unknown top-level key {k!r}")
    runs = doc.get("runs")
    if not isinstance(runs, int) or isinstance(runs, bool) or runs < 1:
        errs.append("runs must be an integer >= 1")
    for k in ("reviewed", "not_reviewed"):
        if not isinstance(doc.get(k), list) or not all(nonempty(x) for x in doc.get(k, [])):
            errs.append(f"{k} must be a list of non-empty strings (use [] if truly nothing)")
    if not doc.get("reviewed") and isinstance(doc.get("reviewed"), list):
        errs.append("reviewed is empty: say what was reviewed")
    fs = doc.get("findings")
    if not isinstance(fs, list):
        return errs + ["findings must be a list"]
    seen = set()
    for i, f in enumerate(fs):
        w = f"finding[{i}]"
        if not isinstance(f, dict):
            errs.append(f"{w}: must be an object")
            continue
        fid = f.get("id")
        w = f"finding {fid!r}"
        for k in sorted(set(f) - KEYS):
            errs.append(f"{w}: unknown key {k!r}")
        for k in REQ:
            if k not in f:
                errs.append(f"{w}: missing {k}")
        if not nonempty(fid):
            errs.append(f"{w}: id must be a non-empty string")
        elif fid in seen:
            errs.append(f"{w}: duplicate id")
        seen.add(fid)
        if f.get("axis") not in AXES:
            errs.append(f"{w}: axis must be one of {AXES}")
        if f.get("severity") not in SEV:
            errs.append(f"{w}: severity must be one of {SEV}")
        if f.get("verdict") not in VERDICTS:
            errs.append(f"{w}: verdict must be one of {VERDICTS}")
        if not nonempty(f.get("file")):
            errs.append(f"{w}: file must be a non-empty string")
        ln = f.get("line")
        if not isinstance(ln, int) or isinstance(ln, bool) or ln < 1:
            errs.append(f"{w}: line must be an integer >= 1")
        if not nonempty(f.get("claim")):
            errs.append(f"{w}: claim must be non-empty")
        ev = f.get("evidence")
        if not isinstance(ev, dict) or set(ev) - {"kind", "ref"} or ev.get("kind") not in KINDS or not nonempty(ev.get("ref")):
            errs.append(f"{w}: evidence needs kind in {KINDS} and a non-empty ref (no evidence, no finding)")
        if f.get("verdict") == "needs_validation" and not nonempty(f.get("check")):
            errs.append(f"{w}: needs_validation must name the check that settles it")
        if f.get("verdict") == "rejected" and not nonempty(f.get("reason")):
            errs.append(f"{w}: rejected must say why")
    return errs


def key(f):
    cls = f.get("class")
    claim = re.sub(r"\W+", " ", f["claim"].lower()).strip()
    return (f["file"], f["line"], cls if nonempty(cls) else claim)


def merge(docs):
    rank = {v: i for i, v in enumerate(VERDICTS)}
    sev = {v: i for i, v in enumerate(SEV)}
    byk = {}
    for d in docs:
        for f in d["findings"]:
            k = key(f)
            cur = byk.get(k)
            if cur is None:
                byk[k] = dict(f)
                continue
            # strongest verdict wins; most severe severity wins; evidence of the winner is kept
            if rank[f["verdict"]] < rank[cur["verdict"]]:
                keep = dict(f)
            else:
                keep = cur
            keep["severity"] = min(f["severity"], cur["severity"], key=lambda s: sev[s])
            byk[k] = keep
    out = sorted(byk.values(), key=lambda f: (f["file"], f["line"], f["id"]))
    seen, n = set(), 0
    for f in out:  # ids from different runs may collide: renumber deterministically
        n += 1
        f["id"] = f"F{n:03d}"
    return {
        "runs": sum(d["runs"] for d in docs),
        "reviewed": sorted({x for d in docs for x in d["reviewed"]}),
        "not_reviewed": sorted({x for d in docs for x in d["not_reviewed"]}),
        "findings": out,
    }


def report(doc, tier):
    sev = {v: i for i, v in enumerate(SEV)}
    fs = doc["findings"]
    order = lambda f: (sev[f["severity"]], f["file"], f["line"], f["id"])
    conf = sorted((f for f in fs if f["verdict"] == "confirmed"), key=order)
    nv = sorted((f for f in fs if f["verdict"] == "needs_validation"), key=order)
    rej = sorted((f for f in fs if f["verdict"] == "rejected"), key=order)
    out = []
    blocking = [f for f in conf if f["severity"] in ("blocker", "major")]
    out.append(f"merge-ready: {'no' if blocking else 'yes'} ({len(blocking)} confirmed blocker/major, {len(nv)} to validate)")
    out.append(f"finder passes: {doc['runs']}" + (f" (tier {tier} needs {MIN_PASSES[tier]})" if tier else ""))
    out.append("")
    out.append("## Confirmed")
    for f in conf:
        out.append(f"- [{f['severity']}] {f['file']}:{f['line']} {f['claim']} | evidence ({f['evidence']['kind']}): {f['evidence']['ref']}" + (f" | fix: {f['fix']}" if nonempty(f.get("fix")) else ""))
    if not conf:
        out.append("- none")
    out.append("")
    out.append("## Needs validation")
    for f in nv:
        out.append(f"- [{f['severity']}] {f['file']}:{f['line']} {f['claim']} | settle by: {f['check']}")
    if not nv:
        out.append("- none")
    out.append("")
    out.append(f"## Rejected ({len(rej)})")
    for f in rej:
        out.append(f"- {f['file']}:{f['line']} {f['claim']} | refuted: {f['reason']}")
    out.append("")
    out.append("## Not reviewed")
    out += [f"- {x}" for x in doc["not_reviewed"]] or ["- nothing excluded (stated explicitly)"]
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate"); v.add_argument("file")
    m = sub.add_parser("merge"); m.add_argument("files", nargs="+"); m.add_argument("-o", required=True)
    r = sub.add_parser("report"); r.add_argument("file"); r.add_argument("--tier", choices=sorted(MIN_PASSES))
    a = ap.parse_args()
    if a.cmd == "validate":
        errs = validate(load(a.file))
    elif a.cmd == "merge":
        docs = [load(f) for f in a.files]
        errs = [f"{f}: {e}" for f, d in zip(a.files, docs) for e in validate(d)]
        if not errs:
            json.dump(merge(docs), open(a.o, "w", encoding="utf-8"), indent=2, sort_keys=True)
            print(f"merged {len(docs)} runs -> {a.o}")
            return 0
    else:
        doc = load(a.file)
        errs = validate(doc)
        if not errs and a.tier and doc["runs"] < MIN_PASSES[a.tier]:
            errs = [f"tier {a.tier} needs {MIN_PASSES[a.tier]} independent finder passes, got {doc['runs']}"]
        if not errs:
            print(report(doc, a.tier))
            return 0
    for e in errs:
        print(f"findings: {e}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
