#!/usr/bin/env python3
"""dst_lint: the deterministic-simulation-testing hard rules as checks, for Python and Kotlin sim test files.
Stdlib only. Run it on your sim test files before you report; fix every ERROR.

  python3 scripts/dst_lint.py <file-or-dir> [...]      exit 1 on any ERROR

A sim file is one that calls run_suite/runSuite (or imports the dst engine). Checks per sim file:
  D1 nondeterminism  no module-level random / time.time / datetime.now / uuid4 / Random.Default / UUID.randomUUID /
                     System.currentTimeMillis / Instant.now (a seeded `random.Random(seed)` inside the engine is fine)
  D2 naming          every test name starts with a mode: regression guard | known bug #<n> | mutant catch | accepted |
                     seed sweep (Python: test_regression_guard_..., test_known_bug_<n>_..., test_mutant_catch_...,
                     test_accepted_..., test_seed_sweep_...); never "bug catch"
  D3 known bugs      a test named known bug goes through known_bug(...)/knownBug(...)
  D4 replay          the replay check stays on: no verify_seeds=0 / verifySeeds = 0 without nonReplayableReason
  D5 anti-vacuity    the file declares at least one sometimes(...)
  D6 mutant catch    a mutant-catch test asserts the violation (expect_violation/expectViolation/assertRaises/pytest.raises)
"""
import os, re, sys

NONDET = [r"(?<![\w.])random\.(random|randint|randrange|choice|choices|shuffle|sample|uniform)\(", r"\btime\.time\b(?!_ns)|\btime\.time_ns\b",
          r"\bdatetime\.(datetime\.)?now\(", r"\buuid\.uuid4\(|\buuid4\(", r"Random\.Default", r"UUID\.randomUUID\(",
          r"System\.currentTimeMillis\(", r"Instant\.now\(", r"\btime\.monotonic\b", r"\btime\.perf_counter\b", r"System\.nanoTime\("]
MODES = ("regression guard", "known bug #", "mutant catch", "accepted", "seed sweep")
PY_TEST = re.compile(r"^\s*def (test_\w+)\s*\(", re.M)
KT_TEST = re.compile(r"fun\s+`([^`]+)`\s*\(")
PY_MODE = re.compile(r"^test_(regression_guard|known_bug_\d+|mutant_catch|accepted|seed_sweep)(_|$)")


def is_sim(text):
    return bool(re.search(r"\brun_suite\(|\brunSuite\(|from dst import|import dst\b", text))


def body_of(text, start):
    """the test body: from its def/fun line to the next non-blank line indented no deeper than it"""
    line_start = text.rfind("\n", 0, start) + 1
    head = text[line_start:].split("\n", 1)
    indent = len(head[0]) - len(head[0].lstrip())
    body = [head[0]]
    for line in (head[1].split("\n") if len(head) > 1 else []):
        if line.strip() and len(line) - len(line.lstrip()) <= indent and not line.strip().startswith((")", "}")): break
        body.append(line)
    return "\n".join(body)


def lint_text(path, text):
    errs = []
    if not is_sim(text): return errs
    code = re.sub(r'"""[\s\S]*?"""|#[^\n]*|//[^\n]*', "", text)
    tests = [(m.group(1), m.start(), "py") for m in PY_TEST.finditer(text)] + [(m.group(1), m.start(), "kt") for m in KT_TEST.finditer(text)]
    def mode(name, lang):
        if lang == "py":
            m = PY_MODE.match(name); return re.match(r"regression_guard|known_bug|mutant_catch|accepted|seed_sweep", m.group(1)).group(0) if m else None
        return next((x.replace(" #", "").replace(" ", "_") for x in MODES if name.startswith(x)), None)
    # a mutant catch may break the harness on purpose (skill section 9: a module-level random draw must trip the replay check)
    exempt = [(p, p + len(body_of(text, p))) for n, p, l in tests if mode(n, l) == "mutant_catch"]
    for pat in NONDET:
        for m in re.finditer(pat, text):
            line = text[text.rfind("\n", 0, m.start()) + 1:m.start()]
            if "#" in line or "//" in line or any(a <= m.start() < b for a, b in exempt): continue
            if re.search(r'"""[\s\S]*$', text[:m.start()]) and text[:m.start()].count('"""') % 2: continue
            errs.append(f"D1 {path}: nondeterminism `{m.group(0)}`: draw from the simulation context / virtual clock")
    for name, pos, lang in tests:
        human = name.replace("_", " ") if lang == "py" else name
        md = mode(name, lang)
        if "bug catch" in human.lower(): errs.append(f"D2 {path}: `{name}`: never name a test 'bug catch'")
        if md is None: errs.append(f"D2 {path}: `{name}` does not start with a mode ({' | '.join(MODES)})")
        body = body_of(text, pos)
        if md == "known_bug" and not re.search(r"\bknown_bug\(|\bknownBug\(", body):
            errs.append(f"D3 {path}: `{name}` is a known-bug test but does not go through known_bug(...)")
        if md == "mutant_catch" and not re.search(r"expect_violation|expectViolation|assertRaises|pytest\.raises|assertFailsWith", body):
            errs.append(f"D6 {path}: `{name}` is a mutant catch but asserts no violation")
    if re.search(r"verify_seeds\s*=\s*0\b|verifySeeds\s*=\s*0\b", code) and "nonReplayableReason" not in text and "non_replayable_reason" not in text:
        errs.append(f"D4 {path}: replay check turned off without a nonReplayableReason")
    if not re.search(r"\.sometimes\(|\bsometimes\(", code): errs.append(f"D5 {path}: no sometimes(...) declared: the sweep can be vacuous")
    return errs


def files(args):
    for a in args:
        if os.path.isdir(a):
            for root, _, fs in os.walk(a):
                for f in sorted(fs):
                    if f.endswith((".py", ".kt")) and f != "dst.py": yield os.path.join(root, f)
        else: yield a


def lint(args):
    errs, n = [], 0
    for p in files(args):
        t = open(p, encoding="utf-8", errors="replace").read()
        if is_sim(t): n += 1
        errs += lint_text(p, t)
    return errs, n


def main(a):
    if not a: print(__doc__); return 2
    errs, n = lint(a)
    for e in errs: print("ERROR " + e)
    print(f"dst_lint: {n} sim file(s), {len(errs)} error(s)")
    return 1 if errs or n == 0 else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
