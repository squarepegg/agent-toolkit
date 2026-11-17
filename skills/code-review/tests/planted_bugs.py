#!/usr/bin/env python3
"""Mutation check: break the scripts one way at a time; the unit tests must fail for every mutant.
A mutant that survives means a behaviour nobody tests. Run: python3 planted_bugs.py"""
import os, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.dirname(HERE)

# (file, original text, replacement, why it matters)
MUTANTS = [
    ("triage.py", "tier_n = min(3, tier_n + 1)", "tier_n = tier_n", "no bump for several material factors"),
    ("triage.py", "tier_n = min(3, tier_n + 1)", "tier_n = tier_n + 1", "bump not capped at T3"),
    ("triage.py", "if material >= cfg", "if material > cfg", "bump off by one"),
    ("triage.py", "base = max(factors.values())", "base = min(factors.values())", "tier uses min not max"),
    ("triage.py", "factors[r[\"factor\"]] = max(factors[r[\"factor\"]], r[\"score\"])", "factors[r[\"factor\"]] = r[\"score\"]", "later rule overwrites a higher score"),
    ("triage.py", "if docs_re.search(path):\n            continue", "if False:\n            continue", "docs/tests raise risk"),
    ("triage.py", "elif line.startswith(\"-\"):", "elif line.startswith(\"~\"):", "removed lines miscounted"),
    ("triage.py", "if line.startswith(\"+\"):", "if line.startswith((\"+\", \"-\")):", "removed lines treated as added"),
    ("triage.py", "if cur is None or line.startswith((\"+++\", \"---\")):", "if cur is None:", "file header lines counted as content"),
    ("triage.py", "for path in sorted(files):", "for path in files:", "output order depends on input order"),
    ("triage.py", "len(risky) >= 2 or", "len(risky) >= 3 or", "mixed-domain split threshold"),
    ("triage.py", "lines > cfg.get(\"split_lines\", 800) and tier_n >= 2)", "lines > cfg.get(\"split_lines\", 800))", "low-risk large change flagged"),
    ("triage.py", "if r.get(\"score\") not in (0, 1, 2, 3) or isinstance(r.get(\"score\"), bool):", "if False:", "bad score accepted"),
    ("triage.py", "if r.get(\"factor\") not in FACTORS:", "if False:", "bad factor accepted"),
    ("findings.py", "or not nonempty(ev.get(\"ref\")):", ":", "evidence ref not required"),
    ("findings.py", "ev.get(\"kind\") not in KINDS or", "False or", "evidence kind not checked"),
    ("findings.py", "if f.get(\"verdict\") == \"needs_validation\" and not nonempty(f.get(\"check\")):", "if False:", "needs_validation without a check"),
    ("findings.py", "if f.get(\"verdict\") == \"rejected\" and not nonempty(f.get(\"reason\")):", "if False:", "rejection without a reason"),
    ("findings.py", "elif fid in seen:", "elif False:", "duplicate ids accepted"),
    ("findings.py", "\"T3\": 2}", "\"T3\": 1}", "T3 needs only one pass"),
    ("findings.py", "doc[\"runs\"] < MIN_PASSES[a.tier]", "False", "pass gate disabled"),
    ("findings.py", "rank[f[\"verdict\"]] < rank[cur[\"verdict\"]]", "rank[f[\"verdict\"]] > rank[cur[\"verdict\"]]", "weakest verdict wins on merge"),
    ("findings.py", "min(f[\"severity\"], cur[\"severity\"], key=lambda s: sev[s])", "cur[\"severity\"]", "merge drops higher severity"),
    ("findings.py", "\"runs\": sum(d[\"runs\"] for d in docs)", "\"runs\": 1", "merge forgets how many passes ran"),
    ("findings.py", "out = sorted(byk.values(), key=lambda f: (f[\"file\"], f[\"line\"], f[\"id\"]))", "out = list(byk.values())", "merge output order depends on input"),
    ("findings.py", "if f[\"severity\"] in (\"blocker\", \"major\")", "if f[\"severity\"] in (\"blocker\",)", "major not blocking"),
    ("findings.py", "conf = sorted((f for f in fs if f[\"verdict\"] == \"confirmed\"), key=order)", "conf = sorted((f for f in fs if f[\"verdict\"] != \"rejected\"), key=order)", "unverified findings block merge"),
    ("findings.py", "errs.append(f\"{k} must be a list of non-empty strings (use [] if truly nothing)\")", "pass", "not_reviewed may be omitted"),
    ("findings.py", "if not errs:\n            json.dump", "if True:\n            json.dump", "merge writes output from invalid input"),
]


def main():
    survived = []
    for i, (fname, old, new, why) in enumerate(MUTANTS):
        with tempfile.TemporaryDirectory() as d:
            shutil.copytree(SKILL, os.path.join(d, "s"), ignore=shutil.ignore_patterns("__pycache__", "planted_bugs.py"))
            p = os.path.join(d, "s", "scripts", fname)
            src = open(p, encoding="utf-8").read()
            if src.count(old) != 1:
                print(f"BAD MUTANT #{i} {why}: pattern matches {src.count(old)} times in {fname}")
                return 2
            open(p, "w", encoding="utf-8").write(src.replace(old, new))
            caught = False
            for t in ("triage_unit_test.py", "findings_unit_test.py", "corpus_unit_test.py"):
                r = subprocess.run([sys.executable, os.path.join(d, "s", "tests", t)], capture_output=True, text=True,
                                   env={**os.environ, "SCRIPTS_DIR": os.path.join(d, "s", "scripts")})
                if r.returncode != 0:
                    caught = True
                    break
            print(("caught   " if caught else "SURVIVED ") + f"#{i:02d} {fname}: {why}")
            if not caught:
                survived.append(why)
    print(f"{len(MUTANTS) - len(survived)}/{len(MUTANTS)} mutants caught")
    return 1 if survived else 0


if __name__ == "__main__":
    sys.exit(main())
