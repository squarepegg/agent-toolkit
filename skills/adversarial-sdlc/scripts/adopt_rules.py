#!/usr/bin/env python3
"""Amendment B3: adopt staged consolidate rules into the project's locked build. A human decides (sdlc.sh asks); this
module does the mechanical part, as one batch: either every selected rule is adopted or none is.

A rule is refused (and then the whole batch) when:
  - it is not staged, or two rounds staged different files under one name;
  - it still matches the project's CURRENT main sources (the code may have changed since it was validated);
  - the target directory already holds a different file of that name, or another rule with the same `id:`
    (rules only add: loosening or removing one is a human edit followed by a relock);
  - gate.config already sets GATE_RULES to another directory.
Adopting copies the files, sets GATE_RULES in gate.config if absent, and appends a record to
.sdlc/consolidate/adopted.jsonl. It prints a JSON object; the caller adds `files` to the lock.
Usage: adopt_rules.py --project P --run R [--rules-dir sdlc-rules] [--how H] [--note N] [--dry-run] [NAME.yml ...]
Exit 0 adopted (or nothing to adopt), 1 batch refused, 2 usage/tool error.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
import time
from pathlib import Path

NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}\.yml$")
CONFIG_LINE = re.compile(r'^GATE_RULES=(?:"([^"]*)"|\'([^\']*)\'|(\S*))\s*$', re.MULTILINE)


def _rv():
    spec = importlib.util.spec_from_file_location("rule_validate", Path(__file__).with_name("rule_validate.py"))
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load rule_validate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def rule_id(text: str) -> str:
    m = re.search(r"^id:\s*(\S+)", text, re.MULTILINE)
    return m[1].strip("'\"") if m else ""


def staged_rules(run: Path) -> tuple[dict[str, Path], list[str]]:
    """name -> staged file, and the conflicts (one name staged with different content in two rounds)."""
    found: dict[str, Path] = {}
    conflicts: list[str] = []
    for f in sorted(run.glob("consolidate/round-*/staged-rules/*.yml")):
        if f.is_symlink() or not f.is_file() or not NAME.match(f.name):
            continue
        if f.name in found and found[f.name].read_bytes() != f.read_bytes():
            conflicts.append(f"{f.name}: staged with different content in {found[f.name].parent.parent.name} and "
                             f"{f.parent.parent.name}")
        found.setdefault(f.name, f)
    return found, conflicts


def configured_dir(config: str) -> str | None:
    m = CONFIG_LINE.search(config)
    return (m[1] if m[1] is not None else m[2] if m[2] is not None else m[3]) if m else None


def rule_message(text: str) -> str:
    """The rule's `message:` as one line (it may be a folded multi-line scalar)."""
    lines = text.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^message:\s*(.*)$", ln)
        if not m:
            continue
        parts = [m[1].strip().strip("'\"")] if m[1].strip() not in ("", ">", "|", ">-", "|-") else []
        for nxt in lines[i + 1:]:
            if not nxt.startswith((" ", "\t")) or not nxt.strip():
                break
            parts.append(nxt.strip())
        return " ".join(p for p in parts if p)
    return ""


def gated_classes(rules_dir: Path) -> str:
    """Markdown for the attacker: the classes of defect the gate already checks mechanically (rule id and message).
    Empty when no rules are adopted. Rule patterns are not shown: the attacker needs the class, not the matcher."""
    rows = []
    for f in sorted(rules_dir.glob("*.yml")) if rules_dir.is_dir() else []:
        if f.is_symlink() or not f.is_file():
            continue
        text = f.read_text()
        rid = rule_id(text)
        if rid:
            rows.append(f"- `{rid}`: {rule_message(text) or '(no message)'}")
    if not rows:
        return ""
    return ("# Classes the gate already checks mechanically\n\n"
            "Every build is scanned for these patterns and fails on a match, so none exists in this code now. Do not spend\n"
            "effort on them; look at what a pattern cannot see.\n\n" + "\n".join(rows) + "\n")


def adopt(project: Path, run: Path, names: list[str], rules_dir: str = "sdlc-rules", how: str = "", note: str = "",
          dry_run: bool = False, sg: str | None = None) -> dict:
    rv = _rv()
    res: dict = {"adopted": [], "already": [], "refused": [], "files": [], "dry_run": dry_run}
    rel = Path(rules_dir)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts or rel.parts[0].startswith(".sdlc"):
        res["refused"].append(f"rules dir {rules_dir!r}: must be a project path outside .sdlc, so the lock covers it")
        return res
    if (project / rel).is_symlink() or project.resolve() not in (project / rel).resolve().parents:
        res["refused"].append(f"rules dir {rules_dir!r}: is a symlink or resolves outside the project")
        return res
    staged, conflicts = staged_rules(run)
    res["refused"] += conflicts
    chosen = sorted(names) if names else sorted(staged)
    for n in chosen:
        if n not in staged:
            res["refused"].append(f"{n}: not staged (see .sdlc/consolidate/round-*/staged-rules/)")
    chosen = [n for n in chosen if n in staged]
    target = project / rel
    config_file = project / "gate.config"
    config = config_file.read_text() if config_file.exists() else ""
    current = configured_dir(config)
    if current is not None and current != rules_dir:
        res["refused"].append(f"gate.config already sets GATE_RULES={current!r}, not {rules_dir!r}")
    existing_ids = {rule_id(f.read_text()): f.name for f in sorted(target.glob("*.yml"))} if target.is_dir() else {}
    sg_bin = rv.find_sg(sg)
    mains = [p for p in (project / x for x in _main_paths(config)) if p.exists()]
    new_ids: dict[str, str] = {}
    to_write: list[tuple[str, str]] = []
    for n in chosen:
        text = staged[n].read_text()
        dest = target / n
        if dest.exists():
            if dest.read_text() == text:
                res["already"].append(n)
                continue
            res["refused"].append(f"{n}: a different file of that name is already adopted (rules only add)")
            continue
        rid = rule_id(text)
        other = existing_ids.get(rid) or new_ids.get(rid)
        if not rid or other:
            res["refused"].append(f"{n}: " + ("no id:" if not rid else f"id {rid!r} already used by {other}"))
            continue
        if not sg_bin:
            raise RuntimeError("ast-grep not found (npm i -g @ast-grep/cli): rules cannot be checked")
        hits = [h for m in mains for h in rv.scan(sg_bin, staged[n], m)]
        if hits:
            res["refused"].append(f"{n}: matches the current main sources ({len(hits)} match(es), first "
                                  f"{hits[0]['file']}:{hits[0]['line']}): fix them or suppress them first")
            continue
        new_ids[rid] = n
        to_write.append((n, text))
    if res["refused"]:
        return res
    res["adopted"] = [n for n, _ in to_write]
    res["files"] = [str(rel / n) for n, _ in to_write]
    if res["adopted"] and current is None:
        res["files"].append("gate.config")
    if dry_run or not res["adopted"]:
        return res
    # The audit record comes first: an adoption nobody can trace must not happen.
    rec = {"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "how": how, "note": note, "dir": rules_dir,
           "rules": {n: hashlib.sha256(t.encode()).hexdigest() for n, t in to_write}}
    log = run / "consolidate" / "adopted.jsonl"
    try:
        log.parent.mkdir(parents=True, exist_ok=True)
        with open(log, "a") as h:
            h.write(json.dumps(rec) + "\n")
    except OSError as e:
        raise OSError(f"cannot record the adoption in {log}: {e}") from e
    target.mkdir(parents=True, exist_ok=True)
    for n, text in to_write:
        (target / n).write_text(text)
    if current is None:
        config_file.write_text(config + ("" if config.endswith("\n") or not config else "\n") + f'GATE_RULES="{rules_dir}"\n')
    return res


def _main_paths(config: str) -> list[str]:
    m = re.search(r'^GATE_MAIN_PATHS=(?:"([^"]*)"|\'([^\']*)\'|(\S*))', config, re.MULTILINE)
    val = (m[1] if m[1] is not None else m[2] if m[2] is not None else m[3]) if m else None
    return (val or "src/main").split()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--project", type=Path)
    ap.add_argument("--run", type=Path)
    ap.add_argument("--rules-dir", default="sdlc-rules")
    ap.add_argument("--how", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--sg")
    ap.add_argument("--gated-classes", type=Path, help="print the attacker-facing list for this rules dir and exit")
    ap.add_argument("names", nargs="*")
    a = ap.parse_args(argv)
    if a.gated_classes is not None:
        sys.stdout.write(gated_classes(a.gated_classes))
        return 0
    if a.project is None or a.run is None:
        ap.error("--project and --run are required")
    try:
        res = adopt(a.project, a.run, a.names, a.rules_dir, a.how, a.note, a.dry_run, a.sg)
    except (OSError, ValueError, RuntimeError) as e:
        print(f"adopt_rules: {e}", file=sys.stderr)
        return 2
    print(json.dumps(res, indent=2))
    return 1 if res["refused"] else 0


if __name__ == "__main__":
    sys.exit(main())
