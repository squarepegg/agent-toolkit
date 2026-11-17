#!/usr/bin/env python3
"""Create, check and list lightweight decision records.

  new_decision.py new "Title" [--repo DIR] [--dir DIR] [--status Proposed] [--dry-run]
  new_decision.py check FILE [FILE ...]
  new_decision.py list [--repo DIR]

Decision directory, first match wins: --dir, docs/decisions, docs/adr, doc/adr,
docs/architecture/decisions, adr. Created as docs/decisions if none exists.
Naming follows the existing files: NNNN-slug.md (default) or YYYY-MM-DD-slug.md.

Records link to commits by trailer:  Decision: docs/decisions/0007-slug.md
`list` shows the commits whose message carries that trailer.

Stdlib only. Python 3.9+. Writes only the new record file.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path

CANDIDATE_DIRS = ["docs/decisions", "docs/adr", "doc/adr", "docs/architecture/decisions", "adr"]
REQUIRED = ["Context", "Options considered", "Decision", "Rejected alternatives", "Assumptions",
            "What would make this wrong"]
MAX_LINES = 60  # one screen
TEMPLATE = Path(__file__).resolve().parent.parent / "templates" / "decision.md"


def git(repo: Path, *args: str) -> str:
    p = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    return p.stdout.strip() if p.returncode == 0 else ""


def repo_root(start: Path) -> Path:
    out = git(start, "rev-parse", "--show-toplevel")
    if not out:
        sys.exit("error: not inside a git repository")
    return Path(out)


def find_dir(root: Path, override: str | None) -> Path:
    if override:
        return root / override
    for d in CANDIDATE_DIRS:
        if (root / d).is_dir() and any((root / d).glob("*.md")):
            return root / d
    return root / "docs/decisions"


def scheme(d: Path) -> str:
    names = [p.name for p in d.glob("*.md")]
    dated = sum(bool(re.match(r"\d{4}-\d{2}-\d{2}-", n)) for n in names)
    numbered = sum(bool(re.match(r"\d{4}-[a-z]", n)) for n in names)
    return "dated" if dated > numbered else "numbered"


def slugify(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:60]


def cmd_new(a) -> int:
    root = repo_root(Path(a.repo).resolve())
    d = find_dir(root, a.dir)
    today = dt.date.today().isoformat()
    if scheme(d) == "dated":
        ident, fname = today, f"{today}-{slugify(a.title)}.md"
    else:
        nums = [int(m.group(1)) for p in d.glob("*.md") if (m := re.match(r"(\d{4})-", p.name))]
        ident = f"{(max(nums) + 1) if nums else 1:04d}"
        fname = f"{ident}-{slugify(a.title)}.md"
    author = git(root, "config", "user.name") or "unknown"
    body = (TEMPLATE.read_text()
            .replace("{{ID}}", ident).replace("{{TITLE}}", a.title).replace("{{STATUS}}", a.status)
            .replace("{{DATE}}", today).replace("{{AUTHOR}}", author))
    path = d / fname
    rel = path.relative_to(root)
    if subprocess.run(["git", "check-ignore", "-q", str(rel)], cwd=root).returncode == 0 and not a.allow_ignored:
        print(f"error: {rel.parent}/ is gitignored in this repo. A record there never reaches\n"
              "teammates, and its Decision: trailer would dangle. Choose a tracked location with\n"
              "--dir, follow the repo's own convention (check README), or pass\n"
              "--allow-ignored to keep a local-only record.", file=sys.stderr)
        if not a.dry_run:
            return 2
    if a.dry_run:
        print(f"# would write {rel}\n")
        print(body)
    else:
        if path.exists():
            sys.exit(f"error: {rel} exists")
        d.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
        print(rel)
    print(f"\nCommit trailer:\n  Decision: {rel}", file=sys.stderr)
    return 0


def check_file(path: Path) -> list[str]:
    text = path.read_text()
    problems = []
    lines = text.splitlines()
    if len(lines) > MAX_LINES:
        problems.append(f"{len(lines)} lines; keep under {MAX_LINES} (one screen)")
    for sec in REQUIRED:
        m = re.search(rf"^##\s+{re.escape(sec)}\s*$(.*?)(?=^##\s|\Z)", text, re.M | re.S | re.I)
        if not m:
            problems.append(f"missing section: {sec}")
            continue
        content = re.sub(r"<!--.*?-->", "", m.group(1), flags=re.S).strip()
        if not content or re.fullmatch(r"(?i)(-\s*)?(todo|tbd|\.\.\.)\.?", content):
            problems.append(f"empty section: {sec}")
    return problems


def cmd_check(a) -> int:
    bad = 0
    for f in a.files:
        probs = check_file(Path(f))
        print(f"{f}: {'ok' if not probs else '; '.join(probs)}")
        bad += bool(probs)
    return 1 if bad else 0


def cmd_list(a) -> int:
    root = repo_root(Path(a.repo).resolve())
    d = find_dir(root, None)
    if not d.is_dir():
        print("no decision records")
        return 0
    print("| record | status | linked commits |\n|---|---|---|")
    for p in sorted(d.glob("*.md")):
        text = p.read_text()
        sm = re.search(r"(?im)^\s*[-*]?\s*\**status\**\s*:\**\s*(.+)$", text)
        status = sm.group(1).strip() if sm else "?"
        rel = str(p.relative_to(root))
        ident = re.match(r"(\d{4}(?:-\d{2}-\d{2})?)", p.name)
        pats = [f"^Decision: {re.escape(rel)}$"] + ([f"^Decision: {ident.group(1)}$"] if ident else [])
        shas = set()
        for pat in pats:
            shas |= set(git(root, "log", "--all", "-E", f"--grep={pat}", "--format=%h").split())
        print(f"| {rel} | {status[:30]} | {', '.join(sorted(shas)) or 'none'} |")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    n = sub.add_parser("new")
    n.add_argument("title")
    n.add_argument("--repo", default=".")
    n.add_argument("--dir")
    n.add_argument("--status", default="Proposed")
    n.add_argument("--dry-run", action="store_true")
    n.add_argument("--allow-ignored", action="store_true", help="write even if the directory is gitignored")
    c = sub.add_parser("check")
    c.add_argument("files", nargs="+")
    ls = sub.add_parser("list")
    ls.add_argument("--repo", default=".")
    a = ap.parse_args()
    return {"new": cmd_new, "check": cmd_check, "list": cmd_list}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
