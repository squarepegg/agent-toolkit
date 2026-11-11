#!/usr/bin/env python3
"""Amendment B1/B2, orchestrator side of the consolidate step. No model runs here.

The consolidate role writes proposals.json plus ast-grep rule files. This module checks them mechanically:
  - every reproduced finding has exactly one proposal of kind rule | behaviour | none, with a reason;
  - a `rule` proposal names a rule file inside the role's rules directory; rule_validate.validate() then decides
    (fires inside the fix's changed lines of the pre-fix snapshot, silent on the fixed one, no unlisted match);
  - accepted rules are copied to a staging directory. They are NOT adopted: a human adopts a batch at the end of the
    run (relock --relayed), so nothing the role wrote reaches the gate by itself.
Usage: consolidate.py --findings F.json --proposals P.json --rules-dir D --pre DIR --fixed DIR --out R.json --stage S
Exit 0 when the report was written (refused rules are a result, not an error), 2 on malformed input.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import shutil
import sys
from pathlib import Path

KINDS = ("rule", "behaviour", "none")
SAFE_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.yml$")


def _rv():
    spec = importlib.util.spec_from_file_location("rule_validate", Path(__file__).with_name("rule_validate.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load rule_validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def reproduced_ids(findings: dict) -> list[str]:
    return [f["id"] for f in findings.get("findings", []) if isinstance(f, dict) and f.get("status") == "reproduced"]


def check_proposals(proposals: object, ids: list[str]) -> tuple[list[dict], list[str]]:
    """The well-formed proposals (one per finding id) and the problems found."""
    problems: list[str] = []
    items = proposals.get("proposals") if isinstance(proposals, dict) else None
    if not isinstance(items, list):
        return [], ["proposals.json must be an object with a 'proposals' list"]
    seen: dict[str, dict] = {}
    for p in items:
        if not isinstance(p, dict) or not isinstance(p.get("finding"), str):
            problems.append(f"malformed proposal: {str(p)[:80]}")
            continue
        fid = p["finding"]
        if fid not in ids:
            problems.append(f"{fid}: not a reproduced finding of this round")
        elif fid in seen:
            problems.append(f"{fid}: more than one proposal")
        elif p.get("kind") not in KINDS:
            problems.append(f"{fid}: kind must be one of {KINDS}")
        elif not str(p.get("reason", "")).strip():
            problems.append(f"{fid}: a reason is required")
        elif p["kind"] == "rule" and not SAFE_NAME.match(str(p.get("rule_file", ""))):
            problems.append(f"{fid}: rule_file must be a plain *.yml file name")
        else:
            seen[fid] = p
    problems += [f"{i}: no proposal" for i in ids if i not in seen and not any(f"{i}:" in x for x in problems)]
    return list(seen.values()), problems


def consolidate(findings: dict, proposals: object, rules_dir: Path, pre: Path, fixed: Path, stage: Path,
                sg: str | None = None) -> dict:
    ids = reproduced_ids(findings)
    ok, problems = check_proposals(proposals, ids)
    rv = _rv()
    rows = []
    for p in ok:
        row = {"finding": p["finding"], "kind": p["kind"], "reason": str(p["reason"])[:400]}
        if p["kind"] == "rule":
            rule = rules_dir / p["rule_file"]
            row["rule_file"] = p["rule_file"]
            if rule.is_symlink() or not rule.is_file():
                row.update(accepted=False, why="rule file missing, or a symlink")
            else:
                v = rv.validate(rule, pre, fixed, fixed, sg=sg)
                row.update(accepted=v["accepted"], why=v["reason"], pre_hits=v["pre_hits_in_hunks"],
                           current_matches=v["current_matches"])  # no timings: the report is byte-for-byte reproducible
                if v["accepted"]:
                    stage.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(rule, stage / p["rule_file"])
        rows.append(row)
    by = {k: sum(1 for r in rows if r["kind"] == k) for k in KINDS}
    return {"reproduced": len(ids), "problems": problems, "by_kind": by,
            "rules_accepted": sum(1 for r in rows if r["kind"] == "rule" and r["accepted"]),
            "rules_refused": sum(1 for r in rows if r["kind"] == "rule" and not r["accepted"]), "rows": rows}


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _jsonl(path: Path) -> list[dict]:
    """The JSON objects of a JSON-lines file; a missing file is empty and an unreadable line is skipped."""
    out: list[dict] = []
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if isinstance(rec, dict):
            out.append(rec)
    return out


def session_usage(session_dir: Path) -> dict:
    """Tokens and cost the provider reported in one role run's transcript(s): {tokens, cost, messages}. Unreadable
    lines are skipped; a missing directory counts nothing."""
    tokens, cost, msgs = 0, 0.0, 0
    for f in sorted(session_dir.glob("*.jsonl")) if session_dir.is_dir() else []:
        try:
            lines = f.read_text(errors="replace").splitlines()
        except OSError:
            continue
        for line in lines:
            try:
                u = (json.loads(line).get("message") or {}).get("usage")
            except (ValueError, AttributeError):
                continue
            if not isinstance(u, dict):
                continue
            total = u.get("totalTokens")
            if not isinstance(total, int):
                total = sum(v for k in ("input", "output", "cacheRead", "cacheWrite") if isinstance(v := u.get(k), int))
            tokens += total
            c = (u.get("cost") or {}).get("total") if isinstance(u.get("cost"), dict) else None
            cost += c if isinstance(c, (int, float)) else 0.0
            msgs += 1
    return {"tokens": tokens, "cost": cost, "messages": msgs}


def token_lines(run: Path) -> list[str]:
    """Tokens per round and role, from roles.jsonl (round-tagged) and the transcripts it points to."""
    totals: dict[int, dict[str, list]] = {}
    unreadable = 0
    for rec in _jsonl(run / "roles.jsonl"):
        rnd = rec.get("round")
        if not isinstance(rnd, int):
            continue
        u = session_usage(Path(rec.get("session_dir") or ""))
        if not u["messages"]:
            unreadable += 1
            continue
        t = totals.setdefault(rnd, {}).setdefault(str(rec.get("role")), [0, 0.0])
        t[0] += u["tokens"]
        t[1] += u["cost"]
    if not totals:
        return ["- Tokens per round: not available (no round-tagged role records with readable transcripts)."]
    out = ["- Tokens per round (as the provider reported them in the role transcripts):"]
    for rnd in sorted(totals):
        parts = [f"{role} {t[0]:,} (${t[1]:.2f})" for role, t in sorted(totals[rnd].items())]
        out.append(f"  - round {rnd}: " + ", ".join(parts))
    if unreadable:
        out.append(f"  - ({unreadable} role run(s) had no readable transcript and are not counted)")
    return out


def report_section(run: Path, main_files: list[Path]) -> list[str]:
    """B5: the report's account of consolidation, as report lines. Empty when no round was consolidated. Every number
    comes from files on disk (round reports, adoption log, gate step records, the sources), never from a model."""
    rounds = sorted(run.glob("consolidate/round-*/report.json"))
    if not rounds:
        return []
    reports = [(r.parent.name, _read_json(r)) for r in rounds]
    reports = [(n, d) for n, d in reports if isinstance(d, dict)]
    kinds = {k: sum(d.get("by_kind", {}).get(k, 0) for _, d in reports) for k in KINDS}
    reproduced = sum(d.get("reproduced", 0) for _, d in reports)
    proposed_rules = kinds["rule"]
    accepted = sum(d.get("rules_accepted", 0) for _, d in reports)
    refused = [(n, r) for n, d in reports for r in d.get("rows", []) if r.get("kind") == "rule" and not r.get("accepted")]
    problems = [f"{n}: {x}" for n, d in reports for x in d.get("problems", [])]
    out = ["", "## Consolidation (findings turned into deterministic checks)", ""]
    out.append(f"- Findings by class over {len(reports)} fix round(s): rule {kinds['rule']}, behaviour {kinds['behaviour']}, "
               f"none {kinds['none']} ({reproduced} reproduced).")
    out.append(f"- Rules proposed {proposed_rules}: accepted by the mechanical check {accepted}, refused {len(refused)}.")
    for n, r in refused[:10]:
        out.append(f"  - {n} {r.get('finding')} `{r.get('rule_file')}`: {str(r.get('why', ''))[:160]}")
    if len(refused) > 10:
        out.append(f"  - (+{len(refused) - 10} more in .sdlc/consolidate/)")
    if problems:
        out.append(f"- Proposals the role got wrong ({len(problems)}): " + "; ".join(problems[:5]))
    adopted: dict[str, str] = {}
    for rec in _jsonl(run / "consolidate" / "adopted.jsonl"):
        adopted.update({n: rec.get("how", "") for n in rec.get("rules", {})})
    staged = {f.name for f in run.glob("consolidate/round-*/staged-rules/*.yml")}
    out.append(f"- Staged {len(staged)}, adopted into the locked build {len(adopted)}"
               + (f" (by: {', '.join(sorted(set(adopted.values())))})" if adopted else "") + "; staged and not adopted: "
               f"{', '.join(sorted(staged - set(adopted))) or 'none'}.")
    ids = sorted({m.group(1) for f in sorted(run.glob("consolidate/round-*/staged-rules/*.yml"))
                  for m in [re.search(r"^id:\s*(\S+)", f.read_text(), re.MULTILINE)] if m})
    recurrences, gates = [], 0
    for st in sorted(run.glob("gate/*/steps/*-static.json")):
        d = _read_json(st)
        if isinstance(d, dict):
            gates += 1
            recurrences += d.get("metrics", {}).get("rule_matches", [])
    out.append(f"- Recurrences caught by the gate without tokens: {len(recurrences)} rule match(es) in {gates} static step(s)"
               + (": " + "; ".join(recurrences[:5]) if recurrences else "") + ".")
    ignores = 0
    for f in main_files:
        try:
            text = f.read_text(errors="replace")
        except OSError:
            continue
        for m in re.finditer(r"ast-grep-ignore:\s*([^\n]*)", text):
            ignores += any(i in m.group(1) for i in ids)
    out.append(f"- Accepted false positives (`ast-grep-ignore` for these rules in main sources): {ignores}.")
    behaviour = [(n, r) for n, d in reports for r in d.get("rows", []) if r.get("kind") == "behaviour"]
    if behaviour:
        out.append("- Behaviour checks staged for the next run (locked tests do not change mid-run):")
        out += [f"  - {n} {r.get('finding')}: {str(r.get('reason', ''))[:160]}" for n, r in behaviour[:10]]
    out += token_lines(run)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    for name in ("findings", "proposals", "rules-dir", "pre", "fixed", "out", "stage"):
        ap.add_argument(f"--{name}", required=True, type=Path)
    ap.add_argument("--sg")
    a = ap.parse_args(argv)
    try:
        findings = json.loads(a.findings.read_text())
        proposals = json.loads(a.proposals.read_text())
        rep = consolidate(findings, proposals, a.rules_dir, a.pre, a.fixed, a.stage, a.sg)
        a.out.write_text(json.dumps(rep, indent=2) + "\n")
    except (OSError, ValueError, RuntimeError, KeyError, TypeError) as e:
        print(f"consolidate: {e}", file=sys.stderr)
        return 2
    print(json.dumps({k: rep[k] for k in ("reproduced", "by_kind", "rules_accepted", "rules_refused")}
                     | {"problems": len(rep["problems"])}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
