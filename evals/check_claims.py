#!/usr/bin/env python3
"""Claims registry check (free, deterministic, CI). Every skill states what it claims and how each claim is proven.

  python3 evals/check_claims.py            check; print the per-skill table; exit 1 on any failure
  python3 evals/check_claims.py --md FILE  also write the table as markdown (evals/STATUS.md embeds it)

evals/claims/<skill-dir>.json = {"skill": "<dir>", "claims": [claim, ...]}. A claim:
  id       unique within the file
  quote    verbatim text from the skill (SKILL.md or any .md under the skill dir; whitespace-insensitive). If the skill
           text changes, the quote stops matching and CI fails: re-map the claim, don't just edit the quote.
  kind     witness  advice proven by an executable experiment in evals/witness (ignored misses, followed catches, fixed passes)
           tool     the skill ships code; `by` lists test files, each run by a .github/workflows/*.yml job
           model    a claim about agent behaviour; `by` lists evals/outcome task ids (laptop runs, ledger in git)
           policy   a convention or choice, not true/false; `why` says why no experiment applies
           gap      should be proven but is not yet; `why` says what is missing (reported, never hidden)
  by       witness ids | test paths | outcome task ids (not for policy/gap)
  rule     optional: the number of the `## Hard rules` item this claim covers
Fails on: missing claims file for a skill, a quote not in the skill, unknown kind or keys, a witness id that does not
exist, a witness no claim cites, a tool test that does not exist or no workflow runs, an unknown outcome task, a
policy/gap with no `why`, a Hard rule with no claim.
"""
import glob
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(HERE, "witness"))
sys.path.insert(0, os.path.join(HERE, "outcome"))
CLAIMS_DIR = os.path.join(HERE, "claims")
KINDS = {"witness", "tool", "model", "policy", "gap"}
KEYS = {"id", "quote", "kind", "by", "why", "rule"}


def norm(s):
    return " ".join(re.sub(r"[*`]", "", s).split()).lower()


def skill_text(d):
    parts = []
    for p in sorted(glob.glob(os.path.join(ROOT, "skills", d, "**", "*.md"), recursive=True)):
        if "/evals/" in p or "/tests/" in p:
            continue
        parts.append(open(p, encoding="utf-8").read())
    return norm("\n".join(parts))


def hard_rules(d):
    text = open(os.path.join(ROOT, "skills", d, "SKILL.md"), encoding="utf-8").read()
    m = re.search(r"^## Hard rules\n(.*?)(?=^## )", text, re.M | re.S)
    return [int(n) for n in re.findall(r"^(\d+)\. ", m.group(1), re.M)] if m else []


def workflows_text():
    return "\n".join(open(p).read() for p in glob.glob(os.path.join(ROOT, ".github", "workflows", "*.yml")))


def ci_runs(path, wf):
    if path in wf:
        return True
    # a glob loop in a workflow, e.g. `for t in skills/x/tests/*_test.py`
    for g in re.findall(r"(skills/[\w./*-]*\*[\w./*-]*)", wf):
        if glob.fnmatch.fnmatch(path, g):
            return True
    return False


def check():
    import core
    import registry as outcome_tasks
    witnesses = core.load_all()
    task_ids = set(outcome_tasks.TASKS)
    wf = workflows_text()
    fails, table, cited = [], [], set()
    skills = sorted(d for d in os.listdir(os.path.join(ROOT, "skills")) if os.path.isfile(os.path.join(ROOT, "skills", d, "SKILL.md")))
    for d in skills:
        path = os.path.join(CLAIMS_DIR, f"{d}.json")
        if not os.path.exists(path):
            fails.append(f"{d}: no evals/claims/{d}.json")
            continue
        doc = json.load(open(path))
        if doc.get("skill") != d:
            fails.append(f"{path}: skill must be {d!r}")
        text, ids, counts, rules_cov = skill_text(d), set(), {k: 0 for k in KINDS}, set()
        for c in doc.get("claims", []):
            cid = c.get("id", "?")
            where = f"{d}/{cid}"
            if set(c) - KEYS:
                fails.append(f"{where}: unknown keys {sorted(set(c) - KEYS)}")
            if cid in ids:
                fails.append(f"{where}: duplicate id")
            ids.add(cid)
            kind = c.get("kind")
            if kind not in KINDS:
                fails.append(f"{where}: kind {kind!r} not in {sorted(KINDS)}")
                continue
            counts[kind] += 1
            q = c.get("quote", "")
            if len(norm(q)) < 12 or norm(q) not in text:
                fails.append(f"{where}: quote not found in the skill text: {q[:80]!r}")
            by = c.get("by", [])
            if kind in ("policy", "gap"):
                if not c.get("why", "").strip():
                    fails.append(f"{where}: {kind} needs a why")
                if by:
                    fails.append(f"{where}: {kind} must not have `by`")
            elif not by:
                fails.append(f"{where}: {kind} needs `by`")
            for b in by:
                if kind == "witness":
                    if b not in witnesses:
                        fails.append(f"{where}: no witness {b!r}")
                    cited.add(b)
                elif kind == "tool":
                    if not os.path.exists(os.path.join(ROOT, b)):
                        fails.append(f"{where}: tool test {b} does not exist")
                    elif not ci_runs(b, wf):
                        fails.append(f"{where}: tool test {b} is not run by any workflow")
                elif kind == "model" and b not in task_ids:
                    fails.append(f"{where}: no outcome task {b!r} (have {sorted(task_ids)})")
            if "rule" in c:
                rules_cov.add(c["rule"])
        missing_rules = [n for n in hard_rules(d) if n not in rules_cov]
        if missing_rules:
            fails.append(f"{d}: Hard rules with no claim: {missing_rules}")
        table.append((d, counts, len(hard_rules(d))))
    for w in witnesses:
        if w not in cited:
            fails.append(f"witness {w} is cited by no claim")
    return fails, table


def render(table):
    lines = ["| skill | witness (CI) | tool (CI) | model (laptop) | policy | gap |", "|---|---|---|---|---|---|"]
    for d, c, _ in table:
        lines.append(f"| {d} | {c['witness']} | {c['tool']} | {c['model']} | {c['policy']} | {c['gap']} |")
    return "\n".join(lines)


def main(argv):
    fails, table = check()
    print(render(table))
    for f in fails:
        print("FAIL " + f)
    if "--md" in argv:
        with open(argv[argv.index("--md") + 1], "w") as f:
            f.write(render(table) + "\n")
    print(f"\n{len(fails)} failures")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
