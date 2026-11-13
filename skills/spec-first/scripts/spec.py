#!/usr/bin/env python3
"""spec-first: parse, lint, accept and lock a spec. Stdlib only, deterministic (no clock, no network).

  spec.py lint   SPEC [--accepted]   exit 0 ok, 1 findings, 2 unreadable
  spec.py accept SPEC                lint --accepted, set Status: accepted, write SPEC.lock (normative hash + base commit)
  spec.py hash   SPEC                print the normative hash
  spec.py check  SPEC                the spec's lock matches (exit 0) or not (exit 1)

Format: see ../templates/spec.md. Normative sections (hashed): Goal, Decisions, Requirements, Edge cases,
Out of scope, Constraints, Checks. Deviations and Open questions are not hashed (the builder writes Deviations).
"""
import hashlib, json, os, re, subprocess, sys

SECTIONS = ["Goal", "Decisions", "Requirements", "Edge cases", "Out of scope", "Constraints", "Checks",
            "Open questions", "Deviations"]
REQUIRED = ["Goal", "Decisions", "Requirements", "Out of scope", "Checks", "Deviations"]
NORMATIVE = ["Goal", "Decisions", "Requirements", "Edge cases", "Out of scope", "Constraints", "Checks"]
DEFAULT_TEST_GLOBS = ["tests/**", "test/**", "**/__tests__/**", "**/test_*", "**/*_test.*", "**/*.test.*",
                      "**/*.spec.*", "**/*Test.*", "**/*Tests.*", "src/test/**"]
IMPL_WORDS = re.compile(r"\b(implement|refactor|class|method|function|helper|variable|import)\b", re.I)
PLACEHOLDER = re.compile(r"\bTBD\b|\bTODO\b|\?\?\?|<[a-z][^>]*>")
ID_LINE = re.compile(r"^-\s+([RE])(\d+):\s*(\S.*)$")
DEC_LINE = re.compile(r"^-\s+D(\d+):\s*(\S.*?)\s*\((confirmed|assumed)\)\s*$")
CHECK_LINE = re.compile(r"^-\s+C(\d+)\s*\[([^\]]*)\]:\s*(.+)$")
CMD = re.compile(r"^`([^`]+)`\s*->\s*(.+)$")
MANUAL = re.compile(r"^manual:\s*(.+?)\s*->\s*see:\s*(.+)$")
CLAUSE_EXIT = re.compile(r"^exit\s+(\d+)$")
CLAUSE_TEXT = re.compile(r'^(stdout|output)\s+(contains|lacks)\s+"((?:[^"\\]|\\.)*)"$')
CLAUSE_RE = re.compile(r"^(stdout|output)\s+matches\s+/(.+)/$")
EMPTY = {"", "(none)", "none", "-", "- (none)", "- none"}


class SpecError(Exception):
    pass


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise SpecError(f"cannot read {path}: {e.strerror}")


def split_sections(text):
    """-> (title, status, {section: [lines]}, duplicate section names)"""
    title, status, cur, secs, dups = None, None, None, {}, []
    for raw in text.splitlines():
        line = raw.rstrip()
        if title is None and line.startswith("# "):
            title = line[2:].strip(); continue
        m = re.match(r"^Status:\s*(\S+)\s*$", line)
        if m and cur is None:
            status = m.group(1); continue
        m = re.match(r"^##\s+(.+?)\s*$", line)
        if m:
            cur = m.group(1)
            if cur in secs: dups.append(cur)
            secs.setdefault(cur, []); continue
        if cur is not None:
            secs[cur].append(line)
    return title, status, secs, dups


def unescape(s):
    return re.sub(r'\\(["\\n])', lambda m: "\n" if m.group(1) == "n" else m.group(1), s)


def parse_clauses(s):
    """'exit 0; stdout contains "x"' -> list of (kind, stream, op, value) or raise SpecError"""
    out = []
    for part in [p.strip() for p in re.split(r';\s*(?=(?:exit|stdout|output)\b)', s.strip())]:
        if not part: continue
        m = CLAUSE_EXIT.match(part)
        if m: out.append(("exit", None, "eq", int(m.group(1)))); continue
        m = CLAUSE_TEXT.match(part)
        if m: out.append(("text", m.group(1), m.group(2), unescape(m.group(3)))); continue
        m = CLAUSE_RE.match(part)
        if m:
            try: re.compile(m.group(2), re.M)
            except re.error as e: raise SpecError(f"bad regex /{m.group(2)}/: {e}")
            out.append(("regex", m.group(1), "matches", m.group(2))); continue
        raise SpecError(f"cannot parse expectation '{part}'")
    return out


def parse(path):
    text = read(path)
    title, status, secs, dups = split_sections(text)
    spec = {"path": path, "title": title, "status": status, "sections": secs, "dups": dups,
            "requirements": {}, "edges": {}, "decisions": [], "checks": [], "errors": [], "warnings": [],
            "test_globs": list(DEFAULT_TEST_GLOBS)}
    err, warn = spec["errors"].append, spec["warnings"].append
    for d in dups: err(f"section '## {d}' appears twice")
    for name, key, prefix in (("Requirements", "requirements", "R"), ("Edge cases", "edges", "E")):
        for line in secs.get(name, []):
            if not line.strip() or line.startswith("  "): continue
            m = ID_LINE.match(line)
            if not m or m.group(1) != prefix:
                err(f"{name}: line is not '- {prefix}<n>: <behaviour>': {line.strip()}"); continue
            rid = prefix + m.group(2)
            if rid in spec[key]: err(f"{name}: duplicate id {rid}")
            spec[key][rid] = m.group(3)
    for line in secs.get("Decisions", []):
        if not line.strip() or line.startswith("  "): continue
        m = DEC_LINE.match(line)
        if not m: err(f"Decisions: line is not '- D<n>: <decision> (confirmed|assumed)': {line.strip()}"); continue
        spec["decisions"].append({"id": "D" + m.group(1), "text": m.group(2), "tag": m.group(3)})
    for line in secs.get("Constraints", []):
        m = re.match(r"^-\s+Tests:\s*(.+)$", line)
        if m: spec["test_globs"] = [g.strip() for g in m.group(1).split(",") if g.strip()]
    seen = set()
    for line in secs.get("Checks", []):
        if not line.strip() or line.startswith("  "): continue
        m = CHECK_LINE.match(line)
        if not m: err(f"Checks: line is not '- C<n> [R1, E1]: `cmd` -> exit 0' or '... manual: <do> -> see: <what>': {line.strip()}"); continue
        cid = "C" + m.group(1)
        if cid in seen: err(f"Checks: duplicate id {cid}")
        seen.add(cid)
        covers = [c.strip() for c in m.group(2).split(",") if c.strip()]
        body = m.group(3).strip()
        chk = {"id": cid, "covers": covers, "kind": None}
        mc, mm = CMD.match(body), MANUAL.match(body)
        if mc:
            try: clauses = parse_clauses(mc.group(2))
            except SpecError as e: err(f"{cid}: {e}"); continue
            if not any(c[0] == "exit" for c in clauses): err(f"{cid}: a command check needs an 'exit N' clause")
            if not any(c[0] != "exit" for c in clauses): warn(f"{cid}: no output clause; only the exit code is checked")
            chk.update(kind="cmd", cmd=mc.group(1), expect=mc.group(2).strip(), clauses=clauses)
        elif mm:
            chk.update(kind="manual", action=mm.group(1), see=mm.group(2))
        else:
            err(f"{cid}: expected '`cmd` -> <clauses>' or 'manual: <do> -> see: <what>'"); continue
        if not covers: err(f"{cid}: names no requirement or edge case")
        spec["checks"].append(chk)
    return spec


def lint(spec, accepted=False):
    errs, warns = list(spec["errors"]), list(spec["warnings"])
    secs = spec["sections"]
    if not spec["title"]: errs.append("no '# <title>' line")
    if spec["status"] not in ("draft", "accepted"): errs.append(f"Status must be draft or accepted, got {spec['status']!r}")
    for s in REQUIRED:
        if s not in secs: errs.append(f"missing section '## {s}'")
    for s in secs:
        if s not in SECTIONS: warns.append(f"unknown section '## {s}'")
    if not spec["requirements"]: errs.append("no requirements")
    if not spec["checks"]: errs.append("no checks")
    if not spec["decisions"] and "Decisions" in secs: warns.append("no decisions recorded")
    ids = set(spec["requirements"]) | set(spec["edges"])
    covered = set()
    for c in spec["checks"]:
        for x in c["covers"]:
            if x not in ids: errs.append(f"{c['id']} names unknown id {x}")
            covered.add(x)
    for x in sorted(ids - covered, key=idkey):
        (errs if accepted else warns).append(f"{x} is not covered by any check")
    for rid, t in spec["requirements"].items():
        if "`" in t or IMPL_WORDS.search(t):
            warns.append(f"{rid} may describe implementation, not behaviour: {t}")
    if accepted:
        oq = [l.strip() for l in secs.get("Open questions", []) if l.strip()]
        if oq and not all(l in EMPTY for l in oq): errs.append("open questions remain: " + "; ".join(l for l in oq if l not in EMPTY))
        dev = [l.strip() for l in secs.get("Deviations", []) if l.strip()]
        if dev: errs.append("Deviations must be empty when a spec is accepted")
        for s in NORMATIVE:
            for l in secs.get(s, []):
                if PLACEHOLDER.search(l): errs.append(f"placeholder left in '## {s}': {l.strip()}")
    return errs, warns


def idkey(x):
    order = {"R": 0, "E": 1}
    return (order.get(x[0], 2), x[0], int(x[1:])) if len(x) > 1 and x[1:].isdigit() else (3, x, 0)


def normative_hash(spec):
    h = hashlib.sha256()
    for s in NORMATIVE:
        lines = [l.rstrip() for l in spec["sections"].get(s, [])]
        body = "\n".join(l for l in lines if l.strip())
        h.update(f"## {s}\n{body}\n".encode())
    return h.hexdigest()


def git(args, cwd):
    p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if p.returncode != 0: raise SpecError(f"git {' '.join(args)}: {p.stderr.strip()}")
    return p.stdout


def repo_root(path):
    return os.path.realpath(git(["rev-parse", "--show-toplevel"], os.path.dirname(os.path.abspath(path)) or ".").strip())


def lock_path(path):
    return path + ".lock"


def check_lock(spec):
    """-> None if the lock matches, else a reason string"""
    lp = lock_path(spec["path"])
    if not os.path.exists(lp): return "spec was never accepted (no .lock file)"
    try: lock = json.loads(read(lp))
    except (ValueError, SpecError): return "lock file is unreadable"
    if spec["status"] != "accepted": return "Status is not accepted"
    if lock.get("hash") != normative_hash(spec): return "normative sections changed since the spec was accepted"
    return None


def cmd_accept(path):
    spec = parse(path)
    errs, warns = lint(spec, accepted=True)
    for w in warns: print(f"warning: {w}")
    if errs:
        for e in errs: print(f"error: {e}")
        return 1
    text = read(path)
    if spec["status"] != "accepted":
        text = re.sub(r"(?m)^Status:\s*\S+\s*$", "Status: accepted", text, count=1)
        with open(path, "w", encoding="utf-8") as f: f.write(text)
        spec = parse(path)
    root = repo_root(path)
    base = git(["rev-parse", "HEAD"], root).strip()
    lock = {"base": base, "hash": normative_hash(spec), "spec": os.path.relpath(os.path.realpath(path), root)}
    with open(lock_path(path), "w", encoding="utf-8") as f:
        json.dump(lock, f, indent=1, sort_keys=True); f.write("\n")
    print(f"accepted: {lock['spec']} hash {lock['hash'][:12]} base {base[:12]}")
    return 0


def main(argv):
    if len(argv) < 2 or argv[0] not in ("lint", "accept", "hash", "check"):
        print(__doc__.strip()); return 2
    cmd, path = argv[0], argv[1]
    try:
        if cmd == "accept": return cmd_accept(path)
        spec = parse(path)
        if cmd == "hash": print(normative_hash(spec)); return 0
        if cmd == "check":
            r = check_lock(spec)
            print(r or "lock matches"); return 1 if r else 0
        errs, warns = lint(spec, accepted="--accepted" in argv[2:] or spec["status"] == "accepted")
        for w in warns: print(f"warning: {w}")
        for e in errs: print(f"error: {e}")
        print(f"{len(errs)} error(s), {len(warns)} warning(s)")
        return 1 if errs else 0
    except SpecError as e:
        print(f"error: {e}"); return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
