#!/usr/bin/env python3
"""Find scar-tissue candidates in a path and join each one to git history.

Scar tissue: magic timeouts, retry counts, broad catches, defensive checks,
special-case branches, fallback chains, index hints, workaround markers.

The scan is a regex heuristic, not a parser. It finds candidates. It does not
decide what matters. Origins it cannot trace are reported as "unknown".

Usage:
  scar_scan.py PATH [PATH ...] [--format md|json] [--max N] [--pr auto|on|off]
               [--include-tests] [--no-repo-wide]

Stdlib only. Python 3.9+. Read-only: runs git and (optionally) gh, writes stdout.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- patterns

CODE_EXT = {
    ".kt": "kotlin", ".kts": "kotlin", ".java": "java", ".scala": "scala",
    ".py": "python", ".ts": "ts", ".tsx": "ts", ".js": "ts", ".jsx": "ts",
    ".go": "go", ".cs": "csharp", ".rs": "rust", ".sql": "sql",
}
CONFIG_EXT = {".yml": "config", ".yaml": "config", ".properties": "config",
              ".conf": "config", ".toml": "config", ".hcl": "config", ".tf": "config"}

SKIP_DIRS = {".git", "build", "out", "target", "dist", "node_modules", ".gradle",
             ".idea", ".venv", "venv", "__pycache__", ".worktrees", "generated",
             ".next", "coverage"}
TEST_HINT = re.compile(r"(^|/)(test|tests|testFixtures|integrationTest|__tests__|spec)(/|$)|"
                       r"(Test|Spec|IT)\.(kt|java)$|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$")

NUM = r"(?<![\w.$])\d[\d_]*(?:\.\d+)?[LlFfDd]?(?![\w])"
TIMING_NAME = r"(?i:timeout|ttl|delay|backoff|interval|idle|deadline|expir|cooldown|window|period|sleep|wait|stale|jitter|poll|linger|keepalive|heartbeat)"
RETRY_NAME = r"(?i:retr(y|ies)|attempts?\b|maxAttempts|backoff)"
LIMIT_NAME = r"(?i:max|min|limit|capacity|threshold|batch|chunk|pool|budget|quota|buffer|size)"

# (category, priority, regex). Lower priority number = traced first.
PATTERNS: list[tuple[str, int, re.Pattern]] = [
    ("marker", 0, re.compile(r"(?i)\b(hack|fixme|xxx|workaround|kludge|do not (remove|change|touch)|don'?t (remove|change|touch)|intentional(ly)?|deliberately|on purpose|legacy|backward[s]? compat)")),
    ("retry", 1, re.compile(RETRY_NAME)),
    ("timing", 1, re.compile(rf"(?:{TIMING_NAME}.*{NUM})|(?:{NUM}.*{TIMING_NAME})|Duration\.(of\w+|parse)\(|\b\d+\.(milliseconds|seconds|minutes|hours)\b|TimeUnit\.|timedelta\(|Thread\.sleep|time\.sleep|\bdelay\(|setTimeout\(")),
    ("broad-catch", 2, re.compile(r"catch\s*\(\s*\w+\s*:\s*(Exception|Throwable|RuntimeException)\s*\)|catch\s*\(\s*(final\s+)?(Exception|Throwable|RuntimeException)\s+\w+\s*\)|\brunCatching\b|except\s*(Exception|BaseException)?\s*(as\s+\w+)?\s*:|\.catch\(|\brecover\(\)|catch\s*(\(\s*\w*\s*\))?\s*\{")),
    ("rethrow-guard", 1, re.compile(r"catch\s*\(\s*\w+\s*:\s*(CancellationException|InterruptedException|OutOfMemoryError|Error)\s*\)|catch\s*\(\s*(InterruptedException|Error)\s+\w+\s*\)|except\s+(KeyboardInterrupt|asyncio\.CancelledError|CancelledError|GeneratorExit)")),
    ("idempotency", 1, re.compile(r"(?i)idempoten|dedup|exactly[-_ ]once|at[-_ ]least[-_ ]once|\block(ed)?\b|\bmutex\b|synchronized|compareAndSet|SELECT .* FOR UPDATE|IF NOT EXISTS")),
    ("special-case", 3, re.compile(r"""==\s*["'][^"']+["']|["'][^"']+["']\s*==|\.equals\(\s*"|equalsIgnoreCase\(\s*"|\bin\s+(setOf|listOf)\(\s*"|\b(code|status|statusCode)\s*(==|!=)\s*\d{3}\b|\bstartsWith\(\s*"|\bendsWith\(\s*\"""")),
    ("fallback", 3, re.compile(r"^\s*\?:|\?:\s*\w+(\.\w+)*\.get\(|\.get\([^)]*\)\s+or\s|\?\?\s")),
    ("index-hint", 2, re.compile(r"(?i)\b(use|force|ignore)\s+index\b|/\*\+|\ballow\s+filtering\b|\bprewhere\b|\bSETTINGS\s+\w+\s*=|\bFINAL\b")),
    ("constant", 4, re.compile(rf"\bconst\s+val\s+\w+\s*(:\s*\w+)?\s*=\s*-?{NUM}|static\s+final\s+\w+\s+[A-Z_][A-Z0-9_]*\s*=\s*-?{NUM}|^\s*[A-Z][A-Z0-9_]{{2,}}\s*(:\s*\w+)?\s*=\s*-?{NUM}")),
    ("limit", 5, re.compile(rf"(?:{LIMIT_NAME}\w*\s*(:\s*\w+)?\s*[=:(]\s*-?{NUM})")),
    ("defensive", 6, re.compile(r"\brequire(NotNull)?\(|\bcheck(NotNull)?\(|\bassert\b|\bif\s*\(?.*(==|is)\s*(null|None)\)?\s*(:|\{)?\s*return\b|\?:\s*return\b|isNullOrBlank\(\)\)?\s*(\{|return)")),
    ("suppression", 6, re.compile(r"@Suppress(Warnings)?\(|#\s*noqa|#\s*type:\s*ignore|eslint-disable|@ts-ignore|//\s*nolint")),
]
CONFIG_PATTERNS: list[tuple[str, int, re.Pattern]] = [
    ("timing", 1, re.compile(rf"{TIMING_NAME}[\w.-]*\s*[:=]\s*['\"]?-?{NUM}")),
    ("retry", 1, re.compile(rf"{RETRY_NAME}[\w.-]*\s*[:=]\s*['\"]?-?{NUM}")),
    ("limit", 5, re.compile(rf"{LIMIT_NAME}[\w.-]*\s*[:=]\s*['\"]?-?{NUM}")),
    ("marker", 0, PATTERNS[0][2]),
]

COMMENT_START = re.compile(r"^\s*(//|/\*|\*|#|--|<!--)")
PR_RE = re.compile(r"\(#(\d+)\)|Merge pull request #(\d+)|/pull/(\d+)")
INCIDENT_RE = re.compile(
    r"(?i)\b(INC-?\d+|SEV-?[0-4]|incident|outage|post-?mortem|hotfix|regression|rollback|"
    r"root cause|rca|paged|on-?call|customer[- ]reported|data loss|prod(uction)? (bug|issue)|observed in prod(uction)?|in prod(uction)?|fail(s|ed|ing)? open|fail(s|ed|ing)? closed|p9[59]|slo|sla|spike|flak(y|e))\b|"
    r"\b[A-Z][A-Z0-9]{1,9}-\d{1,6}\b|app\.clickup\.com/t/\w+|\bCU-\w+")
FIELD = "\x1f"
REC = "\x1e"


# --------------------------------------------------------------------------- data

@dataclass
class Commit:
    sha: str
    author: str
    date: str
    subject: str
    body: str = ""
    pr: str | None = None
    incident_refs: list[str] = field(default_factory=list)


@dataclass
class Candidate:
    file: str
    line: int
    categories: list[str]
    priority: int
    code: str
    nearby_comment: str = ""
    end_line: int = 0
    tokens: list[str] = field(default_factory=list)
    origin: Commit | None = None
    origin_method: str = "unknown"
    line_first_seen: Commit | None = None
    other_origins: list[str] = field(default_factory=list)
    context_tokens: list[str] = field(default_factory=list)
    tests_mentioning: list[str] = field(default_factory=list)
    docs_mentioning: list[str] = field(default_factory=list)
    origin_pr_title: str = ""
    origin_pr_body: str = ""
    last_touch: Commit | None = None
    line_history: list[Commit] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- git

def run(cmd: list[str], cwd: Path, timeout: int = 30) -> str:
    try:
        p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return ""
    return p.stdout if p.returncode == 0 else ""


def git_root(path: Path) -> Path | None:
    out = run(["git", "rev-parse", "--show-toplevel"], path if path.is_dir() else path.parent)
    return Path(out.strip()) if out.strip() else None


LOG_FMT = FIELD.join(["%H", "%an", "%aI", "%s", "%b"]) + REC


def parse_log(out: str) -> list[Commit]:
    commits = []
    for rec in out.split(REC):
        rec = rec.strip("\n")
        if not rec.strip():
            continue
        parts = rec.split(FIELD)
        if len(parts) < 5:
            continue
        sha, author, date, subject, body = parts[:5]
        c = Commit(sha=sha.strip()[:10], author=author, date=date[:10], subject=subject, body=body.strip())
        c.pr = find_pr(subject + "\n" + body)
        c.incident_refs = find_incidents(subject + "\n" + body)
        commits.append(c)
    return commits


def find_pr(text: str) -> str | None:
    m = PR_RE.search(text)
    return next(g for g in m.groups() if g) if m else None


def find_incidents(text: str) -> list[str]:
    seen = []
    for m in INCIDENT_RE.finditer(text):
        t = m.group(0)
        if t.lower() not in [s.lower() for s in seen]:
            seen.append(t)
    return seen[:6]


def pickaxe(root: Path, token: str, rel: str | None, before: str | None = None) -> list[Commit]:
    """Commits that changed the number of occurrences of `token`, oldest first.

    `git log -S --follow` silently misses some matches, so for a file we run with
    and without --follow and merge.
    """
    base = ["git", "log", "-S", token, f"--format={LOG_FMT}"]
    if before:
        base.append(f"{before}~1")
    if rel is None:
        found = parse_log(run(base, root, timeout=90))
    else:
        found = parse_log(run(base + ["--", rel], root, timeout=60))
        known = {c.sha for c in found}
        found += [c for c in parse_log(run(base + ["--follow", "--", rel], root, timeout=60))
                  if c.sha not in known]
    return sorted(found, key=lambda c: c.date)


def file_text_count(root: Path, rel: str, tok: str) -> int:
    try:
        return (root / rel).read_text(encoding="utf-8", errors="replace").count(tok)
    except OSError:
        return 0


def moved_from(root: Path, sha: str, tokens: list[str]) -> str | None:
    """Return a token that commit `sha` deleted from some file (i.e. the code moved)."""
    out = run(["git", "show", sha, "--format=", "--unified=0", "--no-color"], root, timeout=60)
    removed = "\n".join(line[1:] for line in out.splitlines()
                        if line.startswith("-") and not line.startswith("---"))
    # identifiers are too common to prove a move; require a specific line or literal
    return next((t for t in tokens if len(t) >= 12 and not t.isidentifier() and t in removed), None)


def line_history(root: Path, rel: str, line: int) -> list[Commit]:
    out = run(["git", "log", f"-L{line},{line}:{rel}", "-s", f"--format={LOG_FMT}"], root)
    return parse_log(out)


def file_creation(root: Path, rel: str) -> str | None:
    out = run(["git", "log", "--follow", "--diff-filter=A", "--format=%H", "--", rel], root)
    lines = out.split()
    return lines[-1][:10] if lines else None


def merge_pr_for(root: Path, sha: str) -> tuple[str | None, str]:
    """Find the first merge commit on HEAD that brought `sha` in, and its PR number."""
    out = run(["git", "log", "--merges", "--ancestry-path", "--reverse",
               "--format=%H%x1f%s", f"{sha}..HEAD"], root)
    for line in out.splitlines()[:1]:
        msha, subj = line.split(FIELD, 1)
        return find_pr(subj), msha[:10]
    return None, ""


class PRLookup:
    def __init__(self, root: Path, mode: str):
        self.root = root
        self.cache: dict[str, tuple[str, str]] = {}
        self.enabled = False
        if mode == "off" or not shutil.which("gh"):
            return
        if mode == "on":
            self.enabled = True
        else:  # auto
            self.enabled = subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0

    def get(self, pr: str) -> tuple[str, str]:
        if not self.enabled:
            return "", ""
        if pr not in self.cache:
            out = run(["gh", "pr", "view", pr, "--json", "title,body"], self.root, timeout=20)
            try:
                d = json.loads(out)
                self.cache[pr] = (d.get("title", ""), (d.get("body") or "").strip())
            except (json.JSONDecodeError, TypeError):
                self.cache[pr] = ("", "")
        return self.cache[pr]


# --------------------------------------------------------------------------- scan

def iter_files(paths: list[Path], include_tests: bool):
    for p in paths:
        if p.is_file():
            yield p
            continue
        for dirpath, dirnames, filenames in os.walk(p):
            dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS and not d.startswith(".")]
            for f in sorted(filenames):
                fp = Path(dirpath) / f
                if fp.suffix not in CODE_EXT and fp.suffix not in CONFIG_EXT:
                    continue
                if not include_tests and TEST_HINT.search(str(fp)):
                    continue
                yield fp


def preceding_comment(lines: list[str], idx: int, max_lines: int = 12) -> str:
    out = []
    j = idx - 1
    while j >= 0 and len(out) < max_lines:
        s = lines[j].strip()
        if not s:
            break
        if COMMENT_START.match(lines[j]) or s.startswith("*/"):
            out.append(re.sub(r"^(//+|/\*\*?|\*/?|#+|--)\s?", "", s))
            j -= 1
            continue
        if s.startswith("@"):  # annotations between comment and code
            j -= 1
            continue
        break
    # trailing comment on the line itself
    m = re.search(r"\s(//|#)\s(.+)$", lines[idx])
    tail = [m.group(2)] if m else []
    return " ".join(list(reversed(out)) + tail).strip()


IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{5,}")
STRLIT = re.compile(r"\"([^\"\\]{6,80})\"|'([^'\\]{6,80})'")
STOP_IDENTS = {"return", "private", "override", "public", "internal", "string", "String",
               "Exception", "Throwable", "Duration", "require", "import", "package", "suspend",
               "static", "final", "config", "logger", "message", "lowercase", "orEmpty"}


def tokens_for(code: str, file_text: str) -> list[str]:
    """Distinctive search strings for git -S, most specific first."""
    toks: list[str] = []
    stripped = code.strip()
    if 12 <= len(stripped) <= 160 and file_text.count(stripped) == 1:
        toks.append(stripped)
    for m in STRLIT.finditer(code):
        s = m.group(1) or m.group(2)
        if file_text.count(s) <= 2:
            toks.append(s)
    idents = sorted({i for i in IDENT.findall(code)
                     if i not in STOP_IDENTS and file_text.count(i) <= 3},
                    key=lambda i: (file_text.count(i), -len(i)))
    toks += idents[:2]
    if 12 <= len(stripped) <= 160 and file_text.count(stripped) > 1:
        toks.append(stripped)  # non-unique line: last resort, origin is approximate
    seen, uniq = set(), []
    for t in toks:
        if t not in seen:
            seen.add(t)
            uniq.append(t)
    return uniq[:4]


def scan(paths: list[Path], root: Path, include_tests: bool) -> list[Candidate]:
    cands: list[Candidate] = []
    for fp in iter_files(paths, include_tests):
        try:
            text = fp.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        lines = text.splitlines()
        is_config = fp.suffix in CONFIG_EXT
        pats = CONFIG_PATTERNS if is_config else PATTERNS
        for i, line in enumerate(lines):
            if not line.strip() or line.lstrip().startswith(("import ", "package ", "from ")):
                continue
            is_comment = bool(COMMENT_START.match(line))
            cats, prio = [], 99
            for cat, p, rx in pats:
                if is_comment and cat != "marker":
                    continue
                if rx.search(line):
                    cats.append(cat)
                    prio = min(prio, p)
            if not cats:
                continue
            if is_comment and cats == ["marker"]:
                # only keep comment markers that sit directly above code
                nxt = next((ln for ln in lines[i + 1:i + 4] if ln.strip() and not COMMENT_START.match(ln)), None)
                if nxt is None:
                    continue
            c = Candidate(file=str(fp.resolve().relative_to(root)), line=i + 1, categories=cats,
                          priority=prio, code=line.strip()[:200],
                          nearby_comment=preceding_comment(lines, i)[:600])
            c.tokens = tokens_for(line, text)
            nxt = next((ln for ln in lines[i + 1:i + 4] if ln.strip() and not COMMENT_START.match(ln)), "")
            if {"broad-catch", "defensive", "special-case"} & set(cats):
                # a bare `catch (e: Exception) {` is not unique; borrow the next statement
                c.tokens += [t for t in tokens_for(nxt, text) if t not in c.tokens]
            c.context_tokens = [t for t in IDENT.findall(line + " " + nxt) if t not in STOP_IDENTS] + \
                [m.group(1) or m.group(2) for m in STRLIT.finditer(line + " " + nxt)]
            cands.append(c)
    return cands


# --------------------------------------------------------------------------- trace

DOC_EXT = {".md", ".adoc", ".rst", ".txt"}


class TextIndex:
    """In-memory text of tracked test or doc files, for 'what mentions this site'."""

    def __init__(self, root: Path, kind: str):
        self.files: dict[str, str] = {}
        for rel in run(["git", "ls-files"], root).splitlines():
            suf = Path(rel).suffix
            keep = (TEST_HINT.search(rel) and suf in CODE_EXT) if kind == "tests" else suf in DOC_EXT
            if keep:
                try:
                    self.files[rel] = (root / rel).read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass

    @property
    def lower(self) -> dict[str, str]:
        if not hasattr(self, "_lower"):
            self._lower = {f: t.lower() for f, t in self.files.items()}
        return self._lower

    def mentions(self, tokens: list[str]) -> list[str]:
        hits: dict[str, int] = {}
        for t in tokens:
            if len(t) < 8:
                continue
            tl = t.lower()  # HTTP header names and similar are case-insensitive on the wire
            files = [f for f, txt in self.lower.items() if tl in txt]
            if 0 < len(files) <= 3:  # tokens in many tests are not specific to this site
                for f in files:
                    hits[f] = hits.get(f, 0) + 1
        return [f for f, _ in sorted(hits.items(), key=lambda kv: -kv[1])][:5]


def merge_sites(cands: list[Candidate], gap: int = 2, max_span: int = 15) -> list[Candidate]:
    """Collapse adjacent candidate lines in one file into a single site."""
    out: list[Candidate] = []
    for c in sorted(cands, key=lambda c: (c.file, c.line)):
        prev = out[-1] if out else None
        if (prev and prev.file == c.file and c.line - prev.end_line <= gap
                and c.line - prev.line <= max_span and set(prev.categories) & set(c.categories)):
            prev.end_line = c.line
            prev.categories = list(dict.fromkeys(prev.categories + c.categories))
            prev.priority = min(prev.priority, c.priority)
            prev.tokens = list(dict.fromkeys(prev.tokens + c.tokens))
            prev.context_tokens = list(dict.fromkeys(prev.context_tokens + c.context_tokens))
            continue
        c.end_line = c.line
        out.append(c)
    for c in out:
        # whole-line tokens first (in line order), then literals/idents
        c.tokens = c.tokens[:6]
    return out


def trace(c: Candidate, root: Path, prs: PRLookup, repo_wide: bool) -> None:
    hist = line_history(root, c.file, c.line) if c.end_line == c.line else \
        parse_log(run(["git", "log", f"-L{c.line},{c.end_line}:{c.file}", "-s", f"--format={LOG_FMT}"], root))
    c.line_history = hist[:6]
    c.last_touch = hist[0] if hist else None
    c.line_first_seen = hist[-1] if hist else None

    # Origin = pickaxe hit for the most specific token that has one.
    origin: Commit | None = None
    method = "unknown"
    for tok in c.tokens:
        found = pickaxe(root, tok, c.file)
        if not found:
            continue
        if origin is None:
            origin, method = found[0], f"git log -S {tok!r}"
            n = file_text_count(root, c.file, tok)
            if n > 1:
                c.notes.append(f"search text occurs {n} times in file; origin is when the first "
                               "occurrence appeared, this one may be later (check line history)")
        elif found[0].sha != origin.sha:
            c.other_origins.append(f"{found[0].sha} via -S {tok[:40]!r}: {found[0].subject[:60]}")
    if origin is None and c.line_first_seen:
        origin, method = c.line_first_seen, "git log -L (oldest commit touching these lines)"

    # Code moved in from another file (split/extract): the commit that created this
    # file also deleted the same text elsewhere. Search history before that commit.
    created = file_creation(root, c.file)
    if origin is not None and created and origin.sha == created:
        moved_tok = moved_from(root, created, c.tokens)
        if moved_tok and repo_wide:
            found = pickaxe(root, moved_tok, None, before=created)
            if found:
                c.notes.append(f"moved into this file by {created}; traced to earlier location")
                origin, method = found[0], f"git log -S {moved_tok!r} {created}~1 (repo-wide, pre-move)"
            else:
                c.notes.append(f"moved into this file by {created}; earlier origin not found")
        elif moved_tok:
            c.notes.append(f"moved into this file by {created}; pre-move history not searched (--no-repo-wide)")

    c.origin, c.origin_method = origin, method
    if origin and not origin.pr:
        pr, msha = merge_pr_for(root, origin.sha)
        if pr:
            origin.pr = pr
            c.notes.append(f"PR from merge {msha}")
    if origin and origin.pr:
        c.origin_pr_title, body = prs.get(origin.pr)
        c.origin_pr_body = body[:1500]
        if body:
            origin.incident_refs = list(dict.fromkeys(origin.incident_refs + find_incidents(body)))[:6]
    if c.last_touch and origin and c.last_touch.sha != origin.sha:
        c.notes.append(f"last touched by {c.last_touch.sha} ({c.last_touch.subject[:60]}); "
                       "git blame shows this, not the origin")


# --------------------------------------------------------------------------- output

def md_escape(s: str) -> str:
    return s.replace("|", "\\|").replace("\n", " ")


def to_md(cands: list[Candidate], untraced: int, root: Path, head: str) -> str:
    out = [f"# scar_scan: {root.name} @ {head}", "",
           f"{len(cands)} candidates traced" + (f", {untraced} more found but not traced (raise --max)" if untraced else ""),
           "", "| # | location | categories | code | in-code comment | origin | PR | incident refs | tests mentioning | method |",
           "|---|---|---|---|---|---|---|---|---|---|"]
    for n, c in enumerate(cands, 1):
        o = c.origin
        origin = f"{o.sha} {o.date} {o.subject[:70]}" if o else "unknown"
        pr = (f"#{o.pr} {c.origin_pr_title[:50]}" if o and o.pr else "unknown")
        inc = ", ".join(o.incident_refs) if o and o.incident_refs else "none found"
        out.append("| " + " | ".join(md_escape(x) for x in [
            str(n), f"{c.file}:{c.line}" + (f"-{c.end_line}" if c.end_line > c.line else ""), ",".join(c.categories), f"`{c.code[:90]}`",
            c.nearby_comment[:140] or "none", origin, pr, inc,
            ", ".join(Path(t).name for t in c.tests_mentioning) or "none found", c.origin_method[:60]]) + " |")
    out += ["", "## Commit messages and notes", ""]
    for n, c in enumerate(cands, 1):
        o = c.origin
        out.append(f"**{n}. {c.file}:{c.line}" + (f"-{c.end_line}" if c.end_line > c.line else "") + "**")
        if c.line_first_seen and o and c.line_first_seen.sha != o.sha:
            out.append(f"- lines first seen: {c.line_first_seen.sha} {c.line_first_seen.subject[:70]}")
        for alt in c.other_origins:
            out.append(f"- other pickaxe hit: {md_escape(alt)}")
        if o and o.body:
            out.append(f"- origin body: {md_escape(o.body[:400])}")
        if c.origin_pr_body:
            out.append(f"- PR body: {md_escape(c.origin_pr_body[:400])}")
        if len(c.line_history) > 1:
            out.append("- line history: " + "; ".join(f"{h.sha} {h.subject[:50]}" for h in c.line_history))
        if c.docs_mentioning:
            out.append("- docs mentioning: " + ", ".join(c.docs_mentioning))
        for note in c.notes:
            out.append(f"- note: {note}")
        out.append("")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    ap.add_argument("--max", type=int, default=60, help="max candidates to trace (by priority)")
    ap.add_argument("--pr", choices=["auto", "on", "off"], default="auto", help="look up PR bodies with gh")
    ap.add_argument("--include-tests", action="store_true")
    ap.add_argument("--no-repo-wide", action="store_true", help="skip slow repo-wide pickaxe fallback")
    a = ap.parse_args()

    paths = [Path(p).resolve() for p in a.paths]
    for p in paths:
        if not p.exists():
            print(f"error: {p} does not exist", file=sys.stderr)
            return 2
    root = git_root(paths[0])
    if root is None:
        print("error: path is not inside a git repository", file=sys.stderr)
        return 2
    head = run(["git", "rev-parse", "--short", "HEAD"], root).strip() or "unknown"

    cands = merge_sites(scan(paths, root, a.include_tests))
    cands.sort(key=lambda c: (c.priority, c.file, c.line))
    traced, untraced = cands[: a.max], max(0, len(cands) - a.max)
    traced.sort(key=lambda c: (c.file, c.line))
    prs = PRLookup(root, a.pr)
    tests, docs = TextIndex(root, "tests"), TextIndex(root, "docs")
    for i, c in enumerate(traced, 1):
        print(f"\rtracing {i}/{len(traced)}", end="", file=sys.stderr)
        trace(c, root, prs, not a.no_repo_wide)
        c.tests_mentioning = tests.mentions(c.context_tokens)
        c.docs_mentioning = docs.mentions(c.context_tokens)
    print(file=sys.stderr)

    if a.format == "json":
        print(json.dumps({"repo": str(root), "head": head, "untraced": untraced,
                          "pr_lookup": prs.enabled, "candidates": [asdict(c) for c in traced]}, indent=2))
    else:
        print(to_md(traced, untraced, root, head))
    return 0


if __name__ == "__main__":
    sys.exit(main())
