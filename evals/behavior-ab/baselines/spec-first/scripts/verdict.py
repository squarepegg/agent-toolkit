#!/usr/bin/env python3
"""spec-first: turn a reviewer's report into a final verdict, mechanically. Stdlib only.

  verdict.py REPORT --evidence DIR [--out FILE]

REPORT is the reviewer's markdown; only table rows '| <R1|E1> | PASS|FAIL|NOT CHECKED | <evidence> |' count.
Rules (the reviewer can make a verdict stricter, never looser):
  - every requirement and edge case needs exactly one row; a missing one is MISSING
  - a check covering the id that FAILED makes it FAIL; one NOT CHECKED makes it NOT CHECKED
  - PASS needs a citation that holds: a check id (C<n>) that covers the id and PASSED, or <path>:<line>
    in a file the change touched (an edge case: a test file), with the line present; otherwise UNSUPPORTED
  - a guard from evidence.py (spec changed, existing test changed) or a tree that changed since the evidence
    was collected (STALE) blocks DONE
Status DONE only when every id is PASS and nothing blocks. Exit 0 DONE, 1 NOT DONE, 2 bad input.
"""
import hashlib, json, os, re, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import spec as S  # noqa: E402
import evidence as EV  # noqa: E402

ROW = re.compile(r"^\|\s*([RE]\d+)\s*\|\s*(PASS|FAIL|NOT CHECKED)\s*\|(.*)\|\s*$", re.I)
CID = re.compile(r"\bC(\d+)\b")
LOC = re.compile(r"([A-Za-z0-9_./-]+\.[A-Za-z0-9]+):(\d+)(?:-(\d+))?")


def parse_report(text, ids):
    rows, errs = {}, []
    for line in text.splitlines():
        m = ROW.match(line.strip())
        if not m: continue
        rid, verdict, ev = m.group(1).upper(), m.group(2).upper(), m.group(3).strip()
        if rid not in ids: errs.append(f"report row for unknown id {rid}"); continue
        if rid in rows: errs.append(f"report has two rows for {rid}"); continue
        rows[rid] = (verdict, ev)
    return rows, errs


def line_count(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f: return sum(1 for _ in f)
    except OSError: return -1


def valid_citations(rid, ev, data, checks):
    """-> (valid, invalid) lists of citation strings"""
    good, bad = [], []
    for m in CID.finditer(ev):
        cid = "C" + m.group(1); c = checks.get(cid)
        if c is None: bad.append(f"{cid} does not exist")
        elif rid not in c["covers"]: bad.append(f"{cid} does not cover {rid}")
        elif c["status"] != "PASS": bad.append(f"{cid} is {c['status']}")
        else: good.append(cid)
    for m in LOC.finditer(ev):
        path, a, b = m.group(1), int(m.group(2)), int(m.group(3) or m.group(2))
        cite = m.group(0)
        if path not in data["files"] or data["files"][path] == "D": bad.append(f"{cite}: not a file the change touched"); continue
        if rid.startswith("E") and not EV.is_test(path, data["test_globs"]): bad.append(f"{cite}: an edge case needs a test"); continue
        n = line_count(os.path.join(data["root"], path))
        if not (1 <= a <= b <= n): bad.append(f"{cite}: line out of range (file has {n})"); continue
        good.append(cite)
    return good, bad


def decide(rid, row, data, checks):
    cov = [c for c in data["checks"] if rid in c["covers"]]
    failed = [c["id"] for c in cov if c["status"] == "FAIL"]
    unrun = [c["id"] for c in cov if c["status"] == "NOT CHECKED"]
    if row is None:
        return "MISSING", "the report has no row for it"
    verdict, ev = row
    if failed: return "FAIL", f"{', '.join(failed)} failed"
    if verdict == "FAIL": return "FAIL", f"reviewer: {ev}"
    if unrun: return "NOT CHECKED", f"{', '.join(unrun)} did not run"
    if verdict == "NOT CHECKED": return "NOT CHECKED", f"reviewer: {ev}"
    good, bad = valid_citations(rid, ev, data, checks)
    if not good: return "UNSUPPORTED", "PASS without evidence that holds" + (": " + "; ".join(bad) if bad else "")
    return "PASS", ", ".join(good)


def main(argv):
    args = {"--evidence": None, "--out": None}
    pos, i = [], 0
    while i < len(argv):
        if argv[i] in args and i + 1 < len(argv): args[argv[i]] = argv[i + 1]; i += 2
        else: pos.append(argv[i]); i += 1
    if len(pos) != 1 or not args["--evidence"]:
        print(__doc__.strip()); return 2
    try:
        data = json.loads(S.read(os.path.join(args["--evidence"], "evidence.json")))
        text = S.read(pos[0])
    except (S.SpecError, ValueError) as e:
        print(f"error: {e}"); return 2
    ids = data["ids"]
    rows, errs = parse_report(text, set(ids))
    if errs:
        for e in errs: print(f"error: {e}")
        return 2
    checks = {c["id"]: c for c in data["checks"]}
    blocks = list(data["guards"])
    try:
        root = data["root"]
        if S.git(["rev-parse", "HEAD"], root).strip() != data["head"]: blocks.append("STALE: HEAD moved since the evidence was collected")
        else:
            patch = EV.diff_text(root, data["base"], EV.changed_files(root, data["base"]))
            if hashlib.sha256(patch.encode()).hexdigest() != data["diff_sha"]:
                blocks.append("STALE: the working tree changed since the evidence was collected; rerun evidence.py")
        spec = S.parse(os.path.join(root, data["spec"]))
        if S.normative_hash(spec) != data["spec_hash"]: blocks.append("STALE: the spec changed since the evidence was collected")
    except S.SpecError as e:
        blocks.append(f"STALE: cannot re-read the repository: {e}")

    lines = ["| # | Verdict | Evidence |", "|---|---|---|"]
    final = {}
    for rid in ids:
        v, why = decide(rid, rows.get(rid), data, checks)
        final[rid] = v
        lines.append(f"| {rid} | {v} | {why.replace('|', '/')} |")
    done = not blocks and all(v == "PASS" for v in final.values())
    lines += [""] + [f"BLOCKED: {b}" for b in blocks]
    counts = {k: sum(1 for v in final.values() if v == k) for k in ("PASS", "FAIL", "NOT CHECKED", "UNSUPPORTED", "MISSING")}
    lines.append(("STATUS: DONE" if done else "STATUS: NOT DONE") + " (" + ", ".join(f"{k} {n}" for k, n in counts.items() if n) + ")")
    report = "\n".join(lines) + "\n"
    if args["--out"]:
        with open(args["--out"], "w", encoding="utf-8") as f: f.write(report)
    sys.stdout.write(report)
    return 0 if done else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
