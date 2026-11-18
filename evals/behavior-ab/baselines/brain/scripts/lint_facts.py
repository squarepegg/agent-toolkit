#!/usr/bin/env python3
"""Flag stale/broken brain knowledge: anchored code changed since verified_at, old or unverified facts, stale workstreams,
bad `requires` ids, possible secrets, malformed skills, and tour problems. `--strict` exits 1 on hard issues. Env: BRAIN_DIR, BRAIN_REPOS_ROOT."""
import os, sys, glob, re, subprocess, datetime, yaml
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from _fm import frontmatter
import tour_lint

B = os.environ.get("BRAIN_DIR") or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
today = datetime.date.today()
ROOTS = [p for p in (os.environ.get("BRAIN_REPOS_ROOT"), os.path.expanduser("~/projects")) if p]
SECRET = re.compile(r'AKIA[0-9A-Z]{16}|eyJ[\w-]{20,}\.[\w-]{10,}\.[\w-]+|(?i:(password|secret|api[_-]?key|token)\s*[:=]\s*[\'"]?[A-Za-z0-9/+_-]{16,})')
ABSOLUTE = re.compile(r'\b(always|never|guaranteed|impossible|every single)\b', re.I)
HARD = ("PARSE", "SECRET", "FIELD", "REQ")

unverified_facts = [0]   # count of verified: false facts seen by the last lint() call

def anchor_repos(brain=None):
    """Repos named by fact anchors (for CI: which repos to clone so anchors can be checked)."""
    rs = set()
    for p in glob.glob(f"{brain or B}/facts/*.md"):
        for a in (frontmatter(p)[0].get("anchors") or []): rs.add(str(a).partition("@")[0])
    return sorted(r for r in rs if r)

def d(v):
    try: return v if isinstance(v, datetime.date) else datetime.date.fromisoformat(str(v))
    except Exception: return None

def repo_dir(r):
    for root in ROOTS:
        p = os.path.join(root, r)
        if os.path.isdir(os.path.join(p, ".git")): return p

def lint():
    issues = []; unverified_facts[0] = 0
    for p in sorted(glob.glob(f"{B}/facts/*.md")):
        fm, body = frontmatter(p); rel = os.path.relpath(p, B)
        if fm.get("_parse_error"): issues.append(f"PARSE  {rel}: bad frontmatter"); continue
        unver = fm.get("verified") is False
        unverified_facts[0] += unver
        for k in ("title", "confidence") + (("recorded_at",) if unver else ("verified_at",)):
            if not fm.get(k): issues.append(f"FIELD  {rel}: missing {k}")
        if unver:
            ra = d(fm.get("recorded_at"))
            if ra and (today - ra).days > 30: issues.append(f"UNVER  {rel}: unverified for {(today-ra).days}d - verify, fix or delete")
        va = d(fm.get("verified_at")) or d(fm.get("recorded_at"))
        if not unver and va and (today - va).days > 90: issues.append(f"OLD    {rel}: verified {(today-va).days}d ago")
        if not (fm.get("anchors") or fm.get("source") or fm.get("verify_by")): issues.append(f"SOURCE {rel}: no anchors/source/verify_by - say where this comes from")
        if not fm.get("absolutes_ok"):
            for i, line in enumerate(body.splitlines(), 1):
                m = ABSOLUTE.search(line)
                if m: issues.append(f"ABSOLUTE {rel}:{i}: '{m.group(1).lower()}' - back with evidence or reword (absolutes_ok: true to opt out)"); break
        if "instincts" in (fm.get("tags") or []):   # instinct rows: **When** trigger -> **do** action, _Evidence:_ ..., confidence
            for i, line in enumerate(body.splitlines(), 1):
                if line.startswith("- **When**") and not ("**do**" in line and re.search(r"_Evidence:_.*\b(high|medium|low)\b\.", line)):
                    issues.append(f"INSTINCT {rel}:{i}: row needs '**do**', '_Evidence:_ ...' and a confidence (high|medium|low) ending a sentence")
        for a in fm.get("anchors") or []:
            repo, _, path = str(a).partition("@"); rp = repo_dir(repo)
            if not rp: issues.append(f"ANCHOR {rel}: repo not found locally: {repo}"); continue
            path = re.sub(r'^[0-9a-f]{6,40}:', '', path)
            try:
                out = subprocess.run(["git", "-C", rp, "log", "--oneline", f"--since={va or '90 days ago'}", "--", path or "."],
                                     capture_output=True, text=True, timeout=10).stdout.strip().splitlines()
            except Exception as e: out = []; issues.append(f"ANCHOR {rel}: git error {e}")
            if out: issues.append(f"STALE  {rel}: {len(out)} commit(s) touched {repo}:{path} since {va} (latest: {out[0][:70]})")
    for p in sorted(glob.glob(f"{B}/workstreams/*/STATE.md")):
        fm, _ = frontmatter(p); up = d(fm.get("updated"))
        if fm.get("status") in ("active", "blocked") and up and (today - up).days > 21:
            issues.append(f"WS     {os.path.relpath(p, B)}: {fm['status']} but not updated for {(today-up).days}d")
    rd = tour_lint.make_reader(ROOTS)
    for p in sorted(glob.glob(f"{B}/workstreams/*/TOUR.md")):
        for code, ln, msg in tour_lint.lint_file(p, rd): issues.append(f"TOUR-{code} {os.path.relpath(p, B)}:{ln}: {msg}")
    cp = os.path.join(B, "capabilities.yaml"); pp = os.path.join(B, "probes.yaml")
    probe_ids = {str(x.get("id")) for x in (yaml.safe_load(open(pp)) or [])} if os.path.exists(pp) else set()
    if os.path.exists(cp):
        try:
            for c in yaml.safe_load(open(cp)) or []:
                for r in c.get("requires") or []:
                    if str(r) not in probe_ids and not re.match(r'^(vpn|gh|docker|(aws|kube|mcp):[\w.:/@-]+)$', str(r)): issues.append(f"REQ    capabilities.yaml:{c.get('id')}: unknown probe id '{r}'")
        except Exception as e: issues.append(f"PARSE  capabilities.yaml: {e}")
    for p in glob.glob(f"{B}/**/*.md", recursive=True) + glob.glob(f"{B}/*.yaml"):
        if any(x in p for x in ("/digests/", "/drafts/", "/.cache/", "/tests/")): continue
        for i, line in enumerate(open(p, errors="ignore"), 1):
            if SECRET.search(line): issues.append(f"SECRET {os.path.relpath(p, B)}:{i}: possible secret")
    for p in sorted(glob.glob(f"{B}/skills/*/SKILL.md")):
        fm, _ = frontmatter(p); rel = os.path.relpath(p, B); name = os.path.basename(os.path.dirname(p))
        if fm.get("_parse_error"): issues.append(f"PARSE  {rel}: bad frontmatter"); continue
        if fm.get("name") != name: issues.append(f"FIELD  {rel}: name '{fm.get('name')}' != dir '{name}'")
        if not fm.get("description") or len(str(fm["description"])) > 1024: issues.append(f"FIELD  {rel}: description missing or > 1024 chars")
    return issues

if __name__ == "__main__":
    if "--anchor-repos" in sys.argv: print("\n".join(anchor_repos())); sys.exit(0)
    issues = lint(); hard = [i for i in issues if i.split()[0] in HARD]
    print("\n".join(issues) if issues else "brain lint: clean")
    print(f"-- {len(issues)} issue(s), {len(hard)} hard; unverified: {unverified_facts[0]} facts")
    if "--strict" in sys.argv and hard: sys.exit(1)
