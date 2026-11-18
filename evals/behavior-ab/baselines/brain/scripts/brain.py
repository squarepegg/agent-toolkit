#!/usr/bin/env python3
"""Tiny agent-agnostic CLI for the brain. Env: BRAIN_DIR (default: the directory this script lives in, so a clone works wherever it is).
  brain.py search <words or a question>         ranked line search (stop words dropped; prefix matching) over facts/workstreams/decisions/glossary/capabilities/inbox
  brain.py note <kind> <title> [body]           append to inbox/<date>-<session>.md   (kind: gotcha|capability|fact|workstream|decision|correction)
  brain.py ws new <slug> [title]                create workstreams/<slug>/STATE.md from the template
  brain.py ws list                              list workstreams (status, updated)
  brain.py decision <slug> [title]              create decisions/<date>-<slug>.md from the template
  brain.py inbox                                list unprocessed inbox files (for compaction)
  brain.py compact [--write|--archive]          plan inbox compaction (drafts facts/decisions; flags duplicates)
  brain.py init [--org N] [--claude] ...        one-command setup (see scripts/init_brain.py)
  brain.py primer | lint [--strict]             build PRIMER.md / lint the brain"""
import math, os, sys, glob, re, datetime, subprocess
HERE = os.path.dirname(os.path.abspath(__file__)); sys.path.insert(0, HERE)
B = os.environ.get("BRAIN_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KINDS = ("gotcha", "capability", "fact", "workstream", "decision", "correction")
SECRET = re.compile(r'AKIA[0-9A-Z]{16}|eyJ[\w-]{20,}\.[\w-]{10,}\.[\w-]+|(?i:(password|secret|api[_-]?key|token)\s*[:=]\s*[\'"]?[A-Za-z0-9/+_-]{16,})')
SCAN = ("facts", "workstreams", "decisions", "inbox", "skills", "docs")

def slugify(s): return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "untitled"

TOKEN = re.compile(r"[a-z0-9_]+")

STOP = frozenset("the and for are was were how does did you this that what why when where which with from can could not but has have had its our out any all get got use used using into than then there their them they your yours will would should about after before over under again just also only like need needs want now still been being more some such very much".split())
PER_FILE = 3        # at most this many lines per file: one long handoff must not flood the result list
HEAD_WEIGHT = 1.5   # query words found in a file's name / title / description count this much more than body words
HEAD_LINES = 12     # how many leading lines are searched for title-like lines
HEAD_LINE = re.compile(r"^(title:|name:|description:|tags:|aliases:|# )")

def stem(w):
    """Tiny, deterministic suffix stripper so 'emails' finds 'email' and 'deploying' finds 'deploy' (applied to both the index and the query)."""
    if len(w) > 6 and w.endswith("ing"): w = w[:-3]
    elif len(w) > 5 and w.endswith("ed"): w = w[:-2]
    if len(w) > 4 and w.endswith("ies"): return w[:-3] + "y"
    if len(w) > 3 and w.endswith("s") and not w.endswith("ss"): return w[:-1]
    return w

def tokens(text): return [stem(t) for t in TOKEN.findall(text.lower())]

def query_words(query):
    """Natural-language question -> search words: lowercase tokens longer than 2 chars without stop words (falls back to all tokens if nothing is left).
    Order-preserving and de-duplicated, so the same question always gives the same words."""
    raw = TOKEN.findall(query.lower()); kept = [t for t in raw if len(t) > 2 and t not in STOP]
    return list(dict.fromkeys(kept or raw))

def rank(docs, terms, limit=30):
    """Deterministic file-level ranking of (path, lineno, text) docs for the query terms -> [(path, lineno, text)].
    A query term matches any token it prefixes (stemmed), so 'deploy' finds 'deploys'. Rarer terms weigh more (idf over files).
    A line scores the summed weight of the terms it contains; a file scores its best line plus HEAD_WEIGHT x the weight of the terms found in its
    'head' (file name and the `title:` / `name:` / `description:` / `tags:` / `aliases:` / `# ` lines among its first HEAD_LINES lines; use flow style `tags: [a, b]` so a tag list is one line), so a fact whose title says what you asked
    beats one that merely mentions it. Files are ordered by score then path; each contributes at most PER_FILE lines (best first)."""
    qs = sorted({stem(t) for t in (x.lower() for x in terms) if t})
    files = {}
    for p, i, t in docs: files.setdefault(p, []).append((i, t, tokens(t)))
    def has(ts, q): return any(w.startswith(q) for w in ts)
    n = len(files); df = {q: sum(1 for ls in files.values() if any(has(ts, q) for _, _, ts in ls)) for q in qs}
    w = {q: math.log(1 + n / (1 + df[q])) for q in qs}
    ranked = []
    for p, ls in files.items():
        hl = sorted((-round(sum(w[q] for q in qs if has(ts, q)), 9), i, t) for i, t, ts in ls if any(has(ts, q) for q in qs))
        if not hl: continue
        head = tokens(" ".join([os.path.basename(p)] + [t for _, t, _ in ls[:HEAD_LINES] if HEAD_LINE.match(t)]))
        ranked.append((round(hl[0][0] - HEAD_WEIGHT * sum(w[q] for q in qs if has(head, q)), 9), p, hl[:PER_FILE]))
    ranked.sort(key=lambda r: (r[0], r[1]))
    out = [(p, i, t) for _, p, h in ranked for _, i, t in h]
    return out[:limit]

def corpus(brain):
    files = [f for d in SCAN for f in glob.glob(f"{brain}/{d}/**/*.md", recursive=True)] + glob.glob(f"{brain}/*.yaml")
    docs = []
    for f in files:
        if "/processed/" in f: continue
        for i, line in enumerate(open(f, errors="ignore"), 1):
            if line.strip(): docs.append((os.path.relpath(f, brain), i, line.strip()))
    return docs

SNIP = 200

def snippet(text, terms):
    """Lines longer than SNIP are shown as a window around the first query term (the whole line is indexed, so a hit must be visible)."""
    if len(text) <= SNIP: return text
    low = text.lower(); at = [low.find(t.lower()) for t in terms if t and low.find(t.lower()) >= 0]
    start = max(0, min(at) - 60) if at else 0
    return ("…" if start else "") + text[start:start + SNIP] + ("…" if start + SNIP < len(text) else "")

def search(terms, limit=30, brain=None):
    return [f"{p}:{i}: {snippet(t, terms)}" for p, i, t in rank(corpus(brain or B), terms, limit)]

def note(kind, title, body="", brain=None):
    brain = brain or B
    if kind not in KINDS: raise SystemExit(f"kind must be one of {KINDS}")
    if SECRET.search(title + body): raise SystemExit("refusing: looks like a secret. Never write secrets/PII to the brain.")
    os.makedirs(f"{brain}/inbox", exist_ok=True)
    sid = os.environ.get("BRAIN_SESSION") or str(os.getppid())
    p = f"{brain}/inbox/{datetime.date.today()}-{sid}.md"
    with open(p, "a") as f: f.write(f"\n## [{kind}] {title}\n{body}\n")
    return p

def render_tpl(name, brain, **kw):
    t = open(f"{brain}/templates/{name}").read()
    for k, v in kw.items(): t = t.replace(k, v)
    return t

def ws_new(slug, title=None, brain=None):
    brain = brain or B; slug = slugify(slug); p = f"{brain}/workstreams/{slug}/STATE.md"
    if os.path.exists(p): raise SystemExit(f"exists: {p}")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    t = render_tpl("STATE.md", brain, SLUG=slug, TITLE=title or slug, DATE=str(datetime.date.today()))
    open(p, "w").write(t); return p

def decision_new(slug, title=None, brain=None):
    brain = brain or B; os.makedirs(f"{brain}/decisions", exist_ok=True)
    p = f"{brain}/decisions/{datetime.date.today()}-{slugify(slug)}.md"
    if os.path.exists(p): raise SystemExit(f"exists: {p} (decisions are append-only; supersede with a new slug)")
    open(p, "w").write(render_tpl("decision.md", brain, TITLE=title or slug, DATE=str(datetime.date.today()))); return p

def ws_list(brain=None):
    from _fm import frontmatter
    brain = brain or B; out = []
    for p in sorted(glob.glob(f"{brain}/workstreams/*/STATE.md")):
        fm, _ = frontmatter(p); out.append(f"{fm.get('slug', os.path.basename(os.path.dirname(p)))}\t{fm.get('status', '?')}\t{fm.get('updated', '?')}")
    return out

def inbox(brain=None):
    brain = brain or B
    return sorted(f for f in glob.glob(f"{brain}/inbox/*.md") if not f.endswith("README.md"))

def main(a):
    if not a or a[0] in ("-h", "--help"): print(__doc__); return 0
    c, r = a[0], a[1:]
    if c == "search" and r: print("\n".join(search(query_words(" ".join(r)))) or "no matches")
    elif c == "note" and len(r) >= 2: print(note(r[0], r[1], " ".join(r[2:])))
    elif c == "ws" and r[:1] == ["new"] and len(r) >= 2: print(ws_new(r[1], " ".join(r[2:]) or None))
    elif c == "ws" and r[:1] == ["list"]: print("\n".join(ws_list()))
    elif c == "decision" and r: print(decision_new(r[0], " ".join(r[1:]) or None))
    elif c == "inbox": print("\n".join(inbox()) or "inbox empty")
    elif c in ("compact", "init"):
        return subprocess.call([sys.executable, os.path.join(HERE, "compact.py" if c == "compact" else "init_brain.py")] + r)
    elif c in ("primer", "lint"):
        return subprocess.call([sys.executable, os.path.join(HERE, "build_primer.py" if c == "primer" else "lint_facts.py")] + r)
    else: print(__doc__); return 2
    return 0

if __name__ == "__main__": sys.exit(main(sys.argv[1:]))
