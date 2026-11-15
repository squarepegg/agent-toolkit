#!/usr/bin/env python3
"""Tour lint: deterministic checks for workstreams/<slug>/TOUR.md (Cognitive Tours idea, arXiv 2611.08456).
Pure core: parse / render / check take all inputs as arguments (no clock, no git, no filesystem), so results are reproducible.
Staleness is content-based: an anchor stores a hash of the anchored text; stale == hash differs. Agents audit, humans author:
`--pin` only fills hashes and is run by the owner after re-reading the targets.

Waypoint line:  - [ID -> LINK, LINK] **Kind: title** - why. `anchor: repo@path#h:Heading text` `hash: 0123456789ab`
Anchor forms: `#h:<heading>` = that markdown section (to the next heading of the same or higher level) - preferred; `#exists` = file must exist, no
hash (use for fast-moving files where content hashing is noise); `#L10-40` = line range (brittle: inserted lines shift it, prefer #h:); none = whole file.
(`[ID]` alone for no links; hash is sha256 of the whitespace-trimmed anchored text, first 12 hex.)"""
import difflib, hashlib, os, re, subprocess, sys
from dataclasses import dataclass, field

KINDS = ("Need", "Decision", "Design", "Code", "Test", "Risk", "Question")
LINE = re.compile(r'^- \[(?P<id>[A-Za-z0-9_.-]+)(?: -> (?P<links>[^\]]*))?\] \*\*(?P<kind>\w+): (?P<title>.*?)\*\* - (?P<why>.*?)\s*'
                  r'`anchor: (?P<repo>[^@`\s]+)@(?P<path>[^`#]+?)(?:#(?:L(?P<a>\d+)(?:-(?P<b>\d+))?|h:(?P<h>[^`]+?)|(?P<ex>exists)))?`'
                  r'(?: `hash: (?P<hash>[0-9a-f]{12})`)?\s*$')

@dataclass
class Waypoint:
    line: int; id: str; kind: str; title: str; why: str; repo: str; path: str
    start: int = 0; end: int = 0; hash: str = ""; links: tuple = (); heading: str = ""; exists: bool = False

def parse(text):
    """-> (waypoints, bad_lines). Lines starting with '- [' that do not match the grammar are reported, never silently skipped."""
    wps, bad = [], []
    for i, raw in enumerate(text.splitlines(), 1):
        if not raw.startswith("- ["): continue
        m = LINE.match(raw)
        if not m: bad.append(i); continue
        links = tuple(x.strip() for x in (m["links"] or "").split(",") if x.strip())
        a = int(m["a"]) if m["a"] else 0; b = int(m["b"]) if m["b"] else a
        wps.append(Waypoint(i, m["id"], m["kind"], m["title"], m["why"], m["repo"], m["path"].strip(), a, b, m["hash"] or "", links, (m["h"] or "").strip(), bool(m["ex"])))
    return wps, bad

def render(w):
    lk = f" -> {', '.join(w.links)}" if w.links else ""
    rng = f"#h:{w.heading}" if w.heading else "#exists" if w.exists else f"#L{w.start}" + (f"-{w.end}" if w.end and w.end != w.start else "") if w.start else ""
    h = f" `hash: {w.hash}`" if w.hash else ""
    return f"- [{w.id}{lk}] **{w.kind}: {w.title}** - {w.why} `anchor: {w.repo}@{w.path}{rng}`{h}"

def sections(content, heading):
    """Markdown sections whose heading text equals `heading` (case-insensitive, whitespace-normalised) -> [text]. Fenced code is ignored."""
    lines, out, fence = content.replace("\r\n", "\n").split("\n"), [], False
    want, i = _norm(heading), 0
    while i < len(lines):
        l = lines[i]
        if l.lstrip().startswith("```"): fence = not fence
        m = None if fence else re.match(r'^(#{1,6})\s+(.*?)\s*#*\s*$', l)
        if m and _norm(m.group(2)) == want:
            lvl, j, f2 = len(m.group(1)), i + 1, False
            while j < len(lines):
                if lines[j].lstrip().startswith("```"): f2 = not f2
                m2 = None if f2 else re.match(r'^(#{1,6})\s', lines[j])
                if m2 and len(m2.group(1)) <= lvl: break
                j += 1
            out.append("\n".join(lines[i:j])); i = j; continue
        i += 1
    return out

def anchored_text(content, start=0, end=0, heading=""):
    """Anchored text; None if `heading` is not found exactly once."""
    if heading:
        sec = sections(content, heading)
        return "\n".join(x.rstrip() for x in sec[0].split("\n")).strip() if len(sec) == 1 else None
    lines = content.replace("\r\n", "\n").split("\n")
    if start: lines = lines[start - 1:(end or start)]
    return "\n".join(l.rstrip() for l in lines).strip()

def hash_text(content, start=0, end=0, heading=""):
    t = anchored_text(content, start, end, heading)
    return None if t is None else hashlib.sha256(t.encode()).hexdigest()[:12]

def _norm(s): return re.sub(r'\s+', ' ', s).strip().lower()

def check(wps, bad_lines, status, read):
    """read(repo, path) -> file text | None (missing) | raises LookupError (repo not available). -> sorted [(code, line, msg)]"""
    f = []
    for n in bad_lines: f.append(("BADLINE", n, "waypoint line does not match the grammar"))
    seen = {}
    for w in wps:
        if w.kind not in KINDS: f.append(("KIND", w.line, f"unknown kind {w.kind}"))
        if w.id in seen: f.append(("DUPID", w.line, f"id {w.id} also on line {seen[w.id]}"))
        seen.setdefault(w.id, w.line)
    ids = {w.id: w for w in wps}
    for w in wps:
        for l in w.links:
            if l not in ids: f.append(("DANGLING", w.line, f"{w.id} links to unknown {l}"))
            elif l == w.id: f.append(("SELFLINK", w.line, f"{w.id} links to itself"))
        if not re.search(r'[A-Za-z]{3}', w.why): f.append(("NOWHY", w.line, f"{w.id}: empty 'why'"))
        if w.kind == "Question" and not re.search(r'\bowner:\s*\S', w.why): f.append(("NOOWNER", w.line, f"{w.id}: Question needs 'owner: <name>' in its why"))
        kinds_linked = {ids[l].kind for l in w.links if l in ids}
        if w.kind == "Decision" and "Need" not in kinds_linked: f.append(("NONEED", w.line, f"{w.id}: Decision must link to a Need"))
        if w.kind == "Decision" and not kinds_linked & {"Test", "Risk", "Question"}:  f.append(("NOPROOF", w.line, f"{w.id}: Decision must link to a Test, Risk or Question"))
        try: content = read(w.repo, w.path)
        except LookupError: f.append(("NOREPO", w.line, f"{w.id}: repo not available: {w.repo}")); continue
        if content is None: f.append(("BROKEN", w.line, f"{w.id}: {w.repo}@{w.path} no longer exists")); continue
        if w.exists: continue
        if w.heading:
            n = len(sections(content, w.heading))
            if n == 0: f.append(("BROKEN", w.line, f"{w.id}: heading '{w.heading}' not found in {w.path}")); continue
            if n > 1: f.append(("AMBIGUOUS", w.line, f"{w.id}: heading '{w.heading}' appears {n} times in {w.path}")); continue
        if w.start and w.start > len(content.replace("\r\n", "\n").split("\n")): f.append(("BROKEN", w.line, f"{w.id}: line range beyond end of file")); continue
        if not w.hash: f.append(("UNPINNED", w.line, f"{w.id}: no hash - re-read the target, then run --pin")); continue
        if hash_text(content, w.start, w.end, w.heading) != w.hash: f.append(("STALE", w.line, f"{w.id}: anchored text changed - re-read, update the why if needed, then --pin"))
        tgt, why = _norm(anchored_text(content, w.start, w.end, w.heading)), _norm(w.why)
        if len(why) >= 40 and why in tgt: f.append(("COPIED", w.line, f"{w.id}: 'why' is copied from the target; say it in your own words"))
    # reachability: every non-Need must reach a Need through links
    def reaches(w, seen_=()):
        if w.kind == "Need": return True
        return any(reaches(ids[l], seen_ + (w.id,)) for l in w.links if l in ids and l not in seen_ and l != w.id)
    for w in wps:
        if w.kind != "Need" and not reaches(w): f.append(("ORPHAN", w.line, f"{w.id}: no path to a Need"))
    kinds = {w.kind for w in wps}
    for k in ("Need", "Decision"):
        if wps and k not in kinds: f.append(("MISSINGKIND", 0, f"tour has no {k} waypoint"))
    if status == "draft": f.append(("DRAFT", 0, "agent-drafted; owner must rewrite the whys and set status: reviewed"))
    return sorted(f, key=lambda x: (x[1], x[0], x[2]))

def pin(text, wps, read):
    """Return text with `hash:` set to the current anchored-text hash for every resolvable waypoint (owner-run, explicit)."""
    lines = text.split("\n")
    for w in wps:
        try: content = read(w.repo, w.path)
        except LookupError: continue
        if content is None or w.exists: continue
        h = hash_text(content, w.start, w.end, w.heading)
        if h is None: continue
        w.hash = h; lines[w.line - 1] = render(w)
    return "\n".join(lines)

def find_pinned(history, w):
    """history: [(sha, file_text)] newest first. -> (sha, anchored_text) of the newest version whose hash equals w.hash, else None."""
    for sha, content in history:
        if content is not None and hash_text(content, w.start, w.end, w.heading) == w.hash: return sha, anchored_text(content, w.start, w.end, w.heading)
    return None

def review_prompt(w, pinned, current):
    """Deterministic prompt (no LLM) for the owner: what changed under a STALE waypoint, and what to decide. Never edits anything."""
    head = f"### {w.id} {w.kind}: {w.title}\nYour why: {w.why or '(empty)'}\nAnchor: {w.repo}@{w.path}" + (f"#h:{w.heading}" if w.heading else "") + (f" (links: {', '.join(w.links)})" if w.links else "")
    if pinned is None: body = "Pinned version not found in history (rewritten, squashed or out of range). Re-read the target in full."
    else:
        sha, old = pinned
        diff = "\n".join(difflib.unified_diff((old or "").split("\n"), (current or "").split("\n"), f"pinned@{sha}", "now", lineterm="", n=1))
        body = f"Changed since pinned commit {sha}:\n```diff\n{diff}\n```" if diff else f"Text identical to pinned commit {sha} (hash differs only by formatting)."
    return head + "\n" + body + "\n\nDecide: (a) your why still holds -> run --pin; (b) it no longer holds -> rewrite the why or links, then --pin; (c) the decision is reversed -> add a Decision/Question waypoint.\n"

def backtest(history, w):
    """history: [(sha, text)] newest first. -> (comparable_commits, fires): how often STALE would have fired for w (section absent/ambiguous = skipped)."""
    prev, n, fires = None, 0, 0
    for _, content in reversed(history):
        h = hash_text(content, w.start, w.end, w.heading) if content is not None else None
        if h is None: continue
        n += 1
        if prev is not None and h != prev: fires += 1
        prev = h
    return n, fires

def git_history(repo_dir, path, limit=200):
    d, f = os.path.dirname(os.path.join(repo_dir, path)), os.path.basename(path)
    shas = subprocess.run(["git", "-C", d, "log", "--format=%h", f"-n{limit}", "--", f], capture_output=True, text=True).stdout.split()
    return [(x, subprocess.run(["git", "-C", d, "show", f"{x}:./{f}"], capture_output=True, text=True).stdout) for x in shas]

def review(text, wps, read, history):
    out = []
    for w in wps:
        if w.exists or not w.hash: continue
        try: content = read(w.repo, w.path)
        except LookupError: continue
        if content is None or hash_text(content, w.start, w.end, w.heading) in (None, w.hash): continue
        out.append(review_prompt(w, find_pinned(history(w.repo, w.path), w), anchored_text(content, w.start, w.end, w.heading)))
    return "\n".join(out) or "No stale waypoints."

# ---- thin IO shell ----
def make_reader(roots):
    def read(repo, path):
        for r in roots:
            p = os.path.join(r, repo)
            if os.path.isdir(os.path.join(p, ".git")):
                fp = os.path.join(p, path)
                if not os.path.isfile(fp): return None
                with open(fp, errors="ignore") as fh: return fh.read()
        raise LookupError(repo)
    return read

def split_fm(text):
    m = re.match(r'^---\n(.*?)\n---\n?(.*)', text, re.S)
    if not m: return {}, text
    fm = {}
    for l in m.group(1).splitlines():
        k, _, v = l.partition(":")
        if k.strip() and not l.startswith(" "): fm[k.strip()] = v.split("#")[0].strip()
    return fm, m.group(2)

def lint_file(path, read):
    with open(path) as fh: text = fh.read()
    fm, body = split_fm(text); wps, bad = parse(body)
    return check(wps, bad, fm.get("status", ""), read)

if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    roots = [p for p in (os.environ.get("BRAIN_REPOS_ROOT"), os.path.expanduser("~/projects")) if p]
    rd = make_reader(roots)
    if "--backtest" in sys.argv:
        for p in args:
            with open(p) as fh: _, body = split_fm(fh.read())
            tot = [0, 0]
            for w in parse(body)[0]:
                base = next((os.path.join(r, w.repo) for r in roots if os.path.isdir(os.path.join(r, w.repo, ".git"))), None)
                if not base or w.exists: print(f"{w.id:4} {'exists-only/unresolved: never fires'}"); continue
                n, fires = backtest(git_history(base, w.path, 1000), w); tot[0] += n; tot[1] += fires
                print(f"{w.id:4} {w.path[-44:]:44} {n:6} commits {fires:4} fires {fires / max(n - 1, 1):4.0%}")
            print(f"total comparable commits {tot[0]}; STALE would have fired {tot[1]}x")
        sys.exit(0)
    if "--review" in sys.argv:
        def _hist(repo, path):
            base = next((os.path.join(r, repo) for r in roots if os.path.isdir(os.path.join(r, repo, ".git"))), None)
            return git_history(base, path) if base else []
        for p in args:
            with open(p) as fh: _, body = split_fm(fh.read())
            print(review(body, parse(body)[0], rd, _hist))
        sys.exit(0)
    if "--pin" in sys.argv:
        for p in args:
            with open(p) as fh: text = fh.read()
            fm, body = split_fm(text); wps, _ = parse(body); head = text[:len(text) - len(body)]
            with open(p, "w") as fh: fh.write(head + pin(body, wps, rd))
            print(f"pinned {p}")
        sys.exit(0)
    rc = 0
    for p in args:
        for code, ln, msg in lint_file(p, rd): print(f"{code:11} {p}:{ln}: {msg}"); rc = rc or code not in ("DRAFT", "STALE", "UNPINNED")
    sys.exit(1 if rc else 0)
