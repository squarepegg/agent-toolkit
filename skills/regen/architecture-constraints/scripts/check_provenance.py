#!/usr/bin/env python3
"""Check that every architectural fitness function carries a provenance header.

A fitness function is any of:
  - an ArchUnit rule (`ArchRule`, `noClasses()`, `classes()`, `layeredArchitecture()`, `@ArchTest`)
  - any test or rule whose KDoc/Javadoc/docstring contains the `@fitness` tag

Required header fields (see reference/provenance-header.md):
  Rule, Reason, Set by, Source, Revisit when, Regeneration test

A rule whose Reason is missing or unknown must not be enforced. It must be disabled
(`@Disabled`, `@ArchIgnore`, `xtest`, `.config(enabled = false)`, `@pytest.mark.skip`) and listed as
flagged. This script reports any rule enforced without a reason.

Usage:
  check_provenance.py PATH [PATH ...] [--format md|json] [--strict]

--strict exits 1 if any enforced rule lacks a complete header. Stdlib only. Read-only.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

FIELDS = ["Rule", "Reason", "Set by", "Source", "Revisit when", "Regeneration test"]
EMPTY = re.compile(r"^\s*(|todo|tbd|unknown|\?+|n/?a|-)\s*\.?\s*$", re.I)
EXT = {".kt", ".java", ".py", ".ts"}
SKIP = {".git", "build", "out", "target", "node_modules", ".gradle", ".idea"}

ARCH_DECL = re.compile(
    r"(?:val|var|fun|static\s+final\s+ArchRule|ArchRule)\s+`?([\w ]+?)`?\s*(?::\s*ArchRule)?\s*(?:=|\()"
    r"(?=[\s\S]{0,400}?\b(noClasses|classes|layeredArchitecture|onionArchitecture|methods|fields|noMethods|noFields|slices)\(\))")
ARCH_TEST = re.compile(r"@ArchTest")
TEST_DECL = re.compile(r"(?:fun\s+`([^`]+)`|\btest\(\s*\"([^\"]+)\"|\bshould\(\s*\"([^\"]+)\"|^\s*\"([^\"]{6,})\"\s*\{|def\s+(test_\w+)|\bfun\s+(\w+)\s*\()", re.M)
DOC = re.compile(r"/\*\*(.*?)\*/|\"\"\"(.*?)\"\"\"", re.S)
DISABLED = re.compile(r"@Disabled|@ArchIgnore|@Ignore\b|\bxtest\(|enabled\s*=\s*false|@pytest\.mark\.skip|\.skip\(")


def parse_doc(doc: str) -> dict[str, str]:
    lines = [re.sub(r"^\s*\*\s?", "", ln).rstrip() for ln in doc.splitlines()]
    fields: dict[str, str] = {}
    current = None
    for ln in lines:
        m = re.match(r"^\s*(" + "|".join(map(re.escape, FIELDS)) + r")\s*:\s*(.*)$", ln, re.I)
        if m:
            current = next(f for f in FIELDS if f.lower() == m.group(1).lower())
            fields[current] = m.group(2).strip()
        elif current and ln.strip() and not ln.strip().startswith("@"):
            fields[current] = (fields[current] + " " + ln.strip()).strip()
        elif not ln.strip():
            current = None
    return fields


BETWEEN_DOC_AND_DECL = re.compile(
    r"(?:\s|@[\w.]+(?:\((?:\"[^\"]*\"|[^()\"])*\))?"
    r"|\b(?:private|internal|public|protected|static|final|override|open)\b)*")


def annotation_window(src: str, dpos: int, pos: int) -> str:
    """Text from the end of the rule's doc (or the line above it) through the first
    declaration line: where @Disabled-style annotations live."""
    if dpos >= 0:
        start = src.find("*/", dpos)
        start = start + 2 if start != -1 else pos
    else:
        start = max(0, src.rfind("\n", 0, max(0, src.rfind("\n", 0, pos))))
    out = []
    for ln in src[start:].splitlines():
        out.append(ln)
        s = ln.strip()
        if s and not s.startswith("@"):
            break  # first declaration line
    return "\n".join(out)


def rules_in(path: Path) -> list[dict]:
    src = path.read_text(encoding="utf-8", errors="replace")
    is_arch_file = "com.tngtech.archunit" in src or "@fitness" in src
    if not is_arch_file:
        return []
    docs = [(m.start(), m.end(), m.group(1) or m.group(2) or "") for m in DOC.finditer(src)]
    found = []
    seen_pos = set()

    def doc_before(pos: int) -> tuple[str, int]:
        prev = [d for d in docs if d[1] <= pos and not src[d[1]:pos].strip().strip("@").count("\n\n")]
        if not prev:
            return "", -1
        d = prev[-1]
        between = src[d[1]:pos]
        # allow annotations (with quoted args that may contain parens), modifiers and blank
        # space between the doc and the declaration
        if BETWEEN_DOC_AND_DECL.fullmatch(between):
            return d[2], d[0]
        return "", -1

    decl_candidates = [(m.start(), (m.group(1) or "").strip()) for m in ARCH_DECL.finditer(src)]
    candidates = list(decl_candidates)
    for m in ARCH_TEST.finditer(src):
        # `@ArchTest` on a rule declaration is the same rule: keep the declaration, not a second entry
        if any(p > m.end() and BETWEEN_DOC_AND_DECL.fullmatch(src[m.end():p]) for p, _ in decl_candidates):
            continue
        candidates.append((m.start(), ""))
    arch_positions = [p for p, _ in candidates]
    for d in docs:
        if "@fitness" in d[2]:
            after = src[d[1]:d[1] + 300]
            tm = TEST_DECL.search(after)
            if not tm and any(p >= d[1] and BETWEEN_DOC_AND_DECL.fullmatch(src[d[1]:p]) for p in arch_positions):
                continue  # a documented ArchRule: the rule candidate below carries the declaration line, not this doc's */
            name = next((g for g in tm.groups() if g), "") if tm else ""
            candidates.append((d[1] + (tm.start() if tm else 0), name))
    for pos, name in sorted(candidates):
        doc, dpos = doc_before(pos)
        key = dpos if dpos >= 0 else pos
        if key in seen_pos:
            continue
        seen_pos.add(key)
        if not name:
            nm = re.search(r"(?:val|fun)\s+`?([\w ]+?)`?\s*[=(:]", src[pos:pos + 200])
            name = nm.group(1) if nm else "<unnamed>"
        fields = parse_doc(doc)
        missing = [f for f in FIELDS if f not in fields or EMPTY.match(fields[f])]
        disabled = bool(DISABLED.search(annotation_window(src, dpos, pos)))
        line = src.count("\n", 0, pos) + 1
        status = ("ok" if not missing else
                  "flagged (disabled, reason missing)" if disabled and "Reason" in missing else
                  "incomplete header" if "Reason" not in missing else
                  "ENFORCED WITHOUT REASON")
        found.append({"file": str(path), "line": line, "rule": name, "fields": fields,
                      "missing": missing, "disabled": disabled, "status": status})
    return found


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--strict", action="store_true")
    a = ap.parse_args()

    files = []
    for p in map(Path, a.paths):
        if p.is_file():
            files.append(p)
        else:
            files += [f for f in p.rglob("*") if f.suffix in EXT and not (SKIP & set(f.parts))]
    rules = [r for f in sorted(files) for r in rules_in(f)]

    if a.format == "json":
        print(json.dumps(rules, indent=2))
    else:
        print(f"# check_provenance: {len(rules)} fitness functions\n")
        if not rules:
            print("No ArchUnit rules or @fitness-tagged tests found.")
        else:
            print("| file:line | rule | status | missing fields |\n|---|---|---|---|")
            for r in rules:
                print(f"| {Path(r['file']).name}:{r['line']} | {r['rule'][:60]} | {r['status']} | {', '.join(r['missing']) or '-'} |")
    bad = [r for r in rules if r["status"] in ("ENFORCED WITHOUT REASON", "incomplete header") and not r["disabled"]]
    return 1 if a.strict and bad else 0


if __name__ == "__main__":
    sys.exit(main())
