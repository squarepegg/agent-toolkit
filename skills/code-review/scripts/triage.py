#!/usr/bin/env python3
"""Deterministic risk triage for a change, before anyone opens the diff.

  triage.py --base origin/main [--head HEAD] [--rules rules.json] [--json]
  triage.py --diff-file change.diff
  triage.py --files a.py b/c.sql          (path rules only; added-line rules need a diff)

Four factors, each 0-3 (max over matched rules): impact, boundary, detectability, recovery.
Tier = max factor; +1 (cap 3) when at least `bump_when_factors_at_least_2` factors are >= 2.
A change that touches only docs/tests and matches no rule is T0.
Same input and rules -> byte-identical output. No network, no model, no clock.
Exit 0 always on success (the tier is data, not a pass/fail); 2 on bad input.
"""
import argparse, json, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RULES = os.path.join(HERE, "..", "reference", "risk-rules.json")
FACTORS = ("impact", "boundary", "detectability", "recovery")
RULE_KEYS = {"id", "domain", "factor", "score", "path", "added", "why"}


def die(msg):
    print(f"triage: {msg}", file=sys.stderr)
    sys.exit(2)


def load_rules(path):
    try:
        cfg = json.load(open(path, encoding="utf-8"))
    except (OSError, ValueError) as e:
        die(f"cannot read rules {path}: {e}")
    ids = set()
    for r in cfg.get("rules", []):
        if set(r) - RULE_KEYS:
            die(f"rule {r.get('id')}: unknown keys {sorted(set(r) - RULE_KEYS)}")
        if r.get("id") in ids or not r.get("id"):
            die(f"rule id missing or duplicated: {r.get('id')!r}")
        ids.add(r["id"])
        if r.get("factor") not in FACTORS:
            die(f"rule {r['id']}: factor must be one of {FACTORS}")
        if r.get("score") not in (0, 1, 2, 3) or isinstance(r.get("score"), bool):
            die(f"rule {r['id']}: score must be 0..3")
        if "path" not in r and "added" not in r:
            die(f"rule {r['id']}: needs path and/or added")
        for k in ("path", "added"):
            if k in r:
                try:
                    re.compile(r[k])
                except re.error as e:
                    die(f"rule {r['id']}: bad regex in {k}: {e}")
    for t in ("T0", "T1", "T2", "T3"):
        if t not in cfg.get("tiers", {}):
            die(f"rules missing tier {t}")
    return cfg


def parse_diff(text):
    """-> {path: {"added": [lines], "add": n, "del": n}}"""
    files, cur = {}, None
    for line in text.splitlines():
        m = re.match(r"^diff --git a/(.*?) b/(.*)$", line)
        if m:
            cur = files.setdefault(m.group(2), {"added": [], "add": 0, "del": 0})
            continue
        if cur is None or line.startswith(("+++", "---")):
            continue
        if line.startswith("+"):
            cur["added"].append(line[1:])
            cur["add"] += 1
        elif line.startswith("-"):
            cur["del"] += 1
    return files


def triage(files, cfg):
    docs_re = re.compile(cfg.get("docs_tests_path", "$^"))
    factors = {f: 0 for f in FACTORS}
    matches = []
    domains = {}
    for path in sorted(files):
        info = files[path]
        if docs_re.search(path):
            continue  # docs and tests never raise risk (their lines still count towards size)
        for r in cfg["rules"]:
            pm = re.search(r["path"], path) if "path" in r else None
            if "path" in r and not pm:
                continue
            hit = None
            if "added" in r:
                rx = re.compile(r["added"])
                hit = rx.search("\n".join(info["added"]))  # joined, so a rule can span adjacent added lines
                if hit is None:
                    continue
            if r["score"] == 0:
                continue
            factors[r["factor"]] = max(factors[r["factor"]], r["score"])
            matches.append({"rule": r["id"], "file": path, "factor": r["factor"], "score": r["score"], "why": r["why"]})
            d = domains.setdefault(r["domain"], {"score": 0, "files": set()})
            d["score"] = max(d["score"], r["score"])
            d["files"].add(path)
    lines = sum(i["add"] + i["del"] for i in files.values())
    base = max(factors.values())
    material = sum(1 for v in factors.values() if v >= 2)
    tier_n = base
    if material >= cfg.get("bump_when_factors_at_least_2", 2):
        tier_n = min(3, tier_n + 1)
    tier = f"T{tier_n}"
    risky = sorted(k for k, v in domains.items() if v["score"] >= 2)
    split = len(risky) >= 2 or (lines > cfg.get("split_lines", 800) and tier_n >= 2)
    reasons = []
    if len(risky) >= 2:
        reasons.append("risk domains " + ", ".join(risky) + " are mixed; review each separately")
    if lines > cfg.get("split_lines", 800) and tier_n >= 2:
        reasons.append(f"{lines} changed lines exceeds {cfg.get('split_lines', 800)}")
    return {
        "tier": tier,
        "factors": factors,
        "required": cfg["tiers"][tier],
        "files": len(files),
        "changed_lines": lines,
        "matches": matches,
        "split": {"suggested": split, "reasons": reasons,
                  "groups": {k: sorted(domains[k]["files"]) for k in risky} if split else {}},
    }


def render(res):
    out = [f"tier: {res['tier']}  ({res['files']} files, {res['changed_lines']} changed lines)",
           "factors: " + " ".join(f"{k}={v}" for k, v in res["factors"].items()),
           f"required: {res['required']['finder_passes']} finder pass(es); human: {res['required']['human']}"]
    for m in res["matches"]:
        out.append(f"  - {m['rule']} [{m['factor']}={m['score']}] {m['file']}: {m['why']}")
    if res["split"]["suggested"]:
        out.append("split: yes - " + "; ".join(res["split"]["reasons"]))
        for g, fs in res["split"]["groups"].items():
            out.append(f"  group {g}: {', '.join(fs)}")
    else:
        out.append("split: no")
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--base")
    ap.add_argument("--head", default="HEAD")
    ap.add_argument("--diff-file")
    ap.add_argument("--files", nargs="*")
    ap.add_argument("--rules", default=DEFAULT_RULES)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    cfg = load_rules(a.rules)
    if a.diff_file:
        try:
            files = parse_diff(open(a.diff_file, encoding="utf-8", errors="replace").read())
        except OSError as e:
            die(str(e))
    elif a.base:
        mb = subprocess.run(["git", "merge-base", a.base, a.head], capture_output=True, text=True)
        if mb.returncode:
            die(f"git merge-base failed: {mb.stderr.strip()}")
        d = subprocess.run(["git", "diff", "--no-color", "--no-ext-diff", mb.stdout.strip(), a.head],
                           capture_output=True, text=True)
        if d.returncode:
            die(f"git diff failed: {d.stderr.strip()}")
        files = parse_diff(d.stdout)
    elif a.files is not None:
        files = {f: {"added": [], "add": 0, "del": 0} for f in a.files}
    else:
        die("give --base, --diff-file or --files")
    res = triage(files, cfg)
    print(json.dumps(res, indent=2, sort_keys=True) if a.json else render(res))


if __name__ == "__main__":
    main()
