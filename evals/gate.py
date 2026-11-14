#!/usr/bin/env python3
"""Deterministic adopt/reject gate for a candidate (a skill change, an outside skill, a prompt rule). Stdlib only.

  python3 evals/gate.py evals/candidates/<id>.json results/<id>.json [--md summary.md]

The results file comes from `scripts/skill_ab.py <skill> --samples N --out results/<id>.json` (both arms, `with` and
`without`). Model answers are noisy; this verdict is not: the same files give the same verdict. Thresholds live in the
candidate file and are pre-registered: it must be committed and unmodified (git), and the probes must be the ones
that were run (sha256). The model must be pinned (`--model` in EVAL_PI_ARGS, recorded by skill_ab).

Verdicts (exit 0 = ADOPT, 1 = not adopted, 2 = bad input):
  ADOPT         dev gain >= min_gain, no probe and no heldout loss >= regress_by, cost ratio <= max_cost_ratio
  CEILING       `without` already >= ceiling on dev: these probes cannot show a gain (write harder ones, or drop it)
  NO_DELTA      gain < min_gain
  REGRESSION    a probe, or the heldout split, lost >= regress_by correct samples
  TOO_COSTLY    gain is there, cost ratio > max_cost_ratio
  INCONCLUSIVE  more than max_unfinished of the answers timed out
"""
import hashlib, json, os, subprocess, sys
from collections import defaultdict

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULTS = {"min_gain": 2, "ceiling": 0.9, "regress_by": 2, "max_cost_ratio": 1.5, "max_unfinished": 0.1}
KEYS = {"id", "source", "claim", "skill", "samples", "fetch", "with_path", *DEFAULTS}


class BadInput(Exception):
    pass


def sha256(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def validate_candidate(c, filename=None):
    if not isinstance(c, dict):
        raise BadInput("candidate must be a JSON object")
    unknown = set(c) - KEYS
    if unknown:
        raise BadInput(f"unknown candidate keys: {sorted(unknown)}")
    for k in ("id", "source", "claim", "skill"):
        if not isinstance(c.get(k), str) or not c[k]:
            raise BadInput(f"candidate needs a non-empty string `{k}`")
    if filename and os.path.splitext(os.path.basename(filename))[0] != c["id"]:
        raise BadInput("candidate file name must be <id>.json")
    if not isinstance(c.get("samples", 3), int) or c.get("samples", 3) < 1:
        raise BadInput("samples must be a positive integer")
    for k in DEFAULTS:
        if k in c and (not isinstance(c[k], (int, float)) or isinstance(c[k], bool) or c[k] < 0):
            raise BadInput(f"{k} must be a non-negative number")
    f = c.get("fetch")
    if f is not None:
        ref = str((f or {}).get("ref", ""))
        if not (isinstance(f, dict) and f.get("repo") and len(ref) == 40 and all(ch in "0123456789abcdef" for ch in ref)):
            raise BadInput("fetch needs {repo, ref} with ref a full 40-char commit sha (pinned input)")
        if not c.get("with_path"):
            raise BadInput("fetch needs with_path (the skill directory inside the fetched repo)")
    return {**DEFAULTS, **{k: c[k] for k in DEFAULTS if k in c}}


def tally(rows, arm):
    per, cost, unfinished, n = defaultdict(int), 0.0, 0, 0
    for r in rows:
        if r["arm"] != arm:
            continue
        n += 1
        per[(r.get("split", "dev"), r["probe"])] += 1 if (r["correct"] and r.get("finished", True)) else 0
        cost += float(r.get("cost_usd") or 0)
        unfinished += 0 if r.get("finished", True) else 1
    return per, cost, unfinished / max(1, n)


def check_results(c, res):
    if res.get("skill") != c["skill"]:
        raise BadInput(f"results are for skill {res.get('skill')!r}, candidate is for {c['skill']!r}")
    if res.get("samples") != c.get("samples", 3):
        raise BadInput(f"results ran {res.get('samples')} samples, candidate registered {c.get('samples', 3)}")
    if "--model" not in (res.get("pi_args") or []):
        raise BadInput("model not pinned: run skill_ab with EVAL_PI_ARGS='[\"--model\",\"provider/id\"]'")
    rows = res.get("rows") or []
    if not rows:
        raise BadInput("no rows")
    keys = lambda arm: sorted((r.get("split", "dev"), r["probe"], r["n"]) for r in rows if r["arm"] == arm)
    if not keys("without") or keys("with") != keys("without"):
        raise BadInput("both arms (with, without) must cover the same probes and samples")
    want = sorted(set(range(res["samples"])))
    for (sp, pr) in {(r.get("split", "dev"), r["probe"]) for r in rows}:
        if sorted(r["n"] for r in rows if r["arm"] == "with" and r["probe"] == pr) != want:
            raise BadInput(f"probe {pr} does not have exactly {res['samples']} samples per arm")


def decide(c, res):
    """Pure: candidate + results -> (verdict, facts)."""
    p = validate_candidate(c)
    check_results(c, res)
    b, bcost, bunf = tally(res["rows"], "without")
    w, wcost, wunf = tally(res["rows"], "with")
    n = res["samples"]
    total = lambda t, s: sum(v for (sp, _), v in t.items() if sp == s)
    count = lambda s: n * sum(1 for (sp, _) in b if sp == s)
    f = {"dev_without": total(b, "dev"), "dev_with": total(w, "dev"), "dev_n": count("dev"),
         "heldout_without": total(b, "heldout"), "heldout_with": total(w, "heldout"), "heldout_n": count("heldout"),
         "cost_without": round(bcost, 4), "cost_with": round(wcost, 4),
         "cost_ratio": round(wcost / bcost, 2) if bcost > 0 else (1.0 if wcost == 0 else 999.0),
         "regressed": sorted(pr for (sp, pr), v in b.items() if v - w[(sp, pr)] >= p["regress_by"])}
    f["gain"] = f["dev_with"] - f["dev_without"]
    if f["dev_n"] == 0:
        raise BadInput("no dev probes (mark at most some probes \"split\": \"heldout\")")
    if max(bunf, wunf) > p["max_unfinished"]:
        return "INCONCLUSIVE", f
    if f["regressed"] or f["heldout_without"] - f["heldout_with"] >= p["regress_by"]:
        return "REGRESSION", f
    if f["dev_without"] / f["dev_n"] >= p["ceiling"]:
        return "CEILING", f
    if f["gain"] < p["min_gain"]:
        return "NO_DELTA", f
    if f["cost_ratio"] > p["max_cost_ratio"]:
        return "TOO_COSTLY", f
    return "ADOPT", f


def registered(path):
    """The candidate file must equal its committed version (thresholds cannot move after the run)."""
    rel = os.path.relpath(os.path.abspath(path), ROOT)
    head = subprocess.run(["git", "-C", ROOT, "show", f"HEAD:{rel}"], capture_output=True)
    if head.returncode != 0:
        raise BadInput(f"{rel} is not committed: commit the candidate before running (pre-registration)")
    with open(path, "rb") as fh:
        if fh.read() != head.stdout:
            raise BadInput(f"{rel} differs from its committed version")


def line(c, res, verdict, f):
    probes = len({r["probe"] for r in res["rows"]})
    model = res["pi_args"][res["pi_args"].index("--model") + 1] if "--model" in res["pi_args"][:-1] else "?"
    return (f"- {res.get('date', 'YYYY-MM-DD')} | {c['id']} ({c['source']}) | {probes} probes x {res['samples']} samples, {model} | "
            f"dev {f['dev_without']}/{f['dev_n']} -> {f['dev_with']}/{f['dev_n']}, heldout {f['heldout_without']} -> "
            f"{f['heldout_with']}, cost x{f['cost_ratio']} | {verdict}")


def main(argv):
    md = argv[argv.index("--md") + 1] if "--md" in argv else None
    args = [a for a in argv if a not in ("--md", md)]
    if len(args) != 2:
        print(__doc__, file=sys.stderr)
        return 2
    cand_path, res_path = args
    try:
        with open(cand_path) as fh:
            c = json.load(fh)
        validate_candidate(c, cand_path)
        registered(cand_path)
        with open(res_path) as fh:
            res = json.load(fh)
        probes = os.path.join(ROOT, "skills", c["skill"], "evals", "probes.json")
        if res.get("probes_sha256") != sha256(probes):
            raise BadInput("probes.json changed since the run (sha256 mismatch): re-run")
        verdict, f = decide(c, res)
    except (BadInput, KeyError, TypeError, ValueError, OSError) as e:
        print(f"gate: bad input: {e}", file=sys.stderr)
        return 2
    out = line(c, res, verdict, f)
    print(f"{verdict}: {c['id']}\n{json.dumps(f)}\n{out}")
    if md:
        with open(md, "a") as fh:
            fh.write(f"### Eval gate: `{c['id']}` -> **{verdict}**\n\n{c['claim']}\n\n| | without | with |\n|---|---|---|\n"
                     f"| dev correct | {f['dev_without']}/{f['dev_n']} | {f['dev_with']}/{f['dev_n']} |\n"
                     f"| heldout correct | {f['heldout_without']}/{f['heldout_n']} | {f['heldout_with']}/{f['heldout_n']} |\n"
                     f"| cost USD | {f['cost_without']} | {f['cost_with']} |\n\n"
                     f"Regressed probes: {', '.join(f['regressed']) or 'none'}\n\n"
                     + ("" if verdict == "ADOPT" else f"Add to `evals/negative-results.md`:\n```\n{out}\n```\n"))
    return 0 if verdict == "ADOPT" else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
