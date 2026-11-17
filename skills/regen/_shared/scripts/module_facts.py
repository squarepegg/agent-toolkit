#!/usr/bin/env python3
"""Collect structural facts about a module, for deletion-test and compaction-pass.

Facts, not judgments: size, declared concepts, interfaces and their implementations,
usage counts, imports in and out, write sites (tables, topics, outbound mutations),
contract artifacts, the tests that exercise the module and how heavy they are, and a
count of scar-tissue candidates.

Usage:
  module_facts.py PATH [PATH ...] [--format md|json]

Stdlib only. Python 3.9+. Read-only. Regex heuristics: Kotlin/Java first, Python and
TypeScript roughly.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import scar_scan  # noqa: E402  (shared patterns and file walking)

SRC_EXT = {".kt", ".java", ".py", ".ts", ".tsx", ".go", ".scala"}
DECL = {
    "kotlin": re.compile(r"^\s*(?:(?:public|internal|private|protected|abstract|open|sealed|data|enum|value|inline|annotation|fun)\s+)*"
                         r"(interface|class|object)\s+([A-Z]\w*)", re.M),
    "java": re.compile(r"^\s*(?:(?:public|private|protected|abstract|final|static|sealed)\s+)*(interface|class|enum|record)\s+([A-Z]\w*)", re.M),
    "python": re.compile(r"^(class)\s+([A-Z]\w*)", re.M),
    "ts": re.compile(r"^\s*export\s+(?:default\s+)?(?:abstract\s+)?(interface|class|type)\s+([A-Z]\w*)", re.M),
}
TOP_FUN = {
    "kotlin": re.compile(r"^(?:(?:public|internal|private|suspend|inline)\s+)*fun\s+(?:<[^>]+>\s*)?([a-z]\w*)\s*\(", re.M),
    "python": re.compile(r"^def\s+([a-z_]\w*)\s*\(", re.M),
    "ts": re.compile(r"^export\s+(?:async\s+)?function\s+([a-z]\w*)", re.M),
}
PKG = re.compile(r"^\s*package\s+([\w.]+)", re.M)
IMPORT = re.compile(r"^\s*import\s+(?:static\s+)?([\w.]+)", re.M)
PY_IMPORT = re.compile(r"^\s*(?:from\s+([\w.]+)\s+import|import\s+([\w.]+))", re.M)

WRITE_PATTERNS = [
    ("sql-write", re.compile(r"(?i)\b(insert\s+into|update\s+[\w.\"`]+\s+set|delete\s+from|merge\s+into|upsert\s+into|truncate\s+table)\s+[\"`]?([\w.]+)?")),
    ("ddl", re.compile(r"(?i)\b(create|alter|drop)\s+(table|index|materialized view|type)\s+(if\s+(not\s+)?exists\s+)?[\"`]?([\w.]+)?")),
    ("repo-write", re.compile(r"\.(save|saveAll|insert|update|upsert|delete|deleteById|persist|merge)\s*\(")),
    ("message-produce", re.compile(r"(?i)(producer\.send|kafkaTemplate|@KafkaClient|\.produce\(|publish\(|\.emit\(|sendMessage\()")),
    ("outbound-mutation", re.compile(r"(?i)HttpRequest\s*\.\s*(POST|PUT|PATCH|DELETE)\b|\.(POST|PUT|PATCH|DELETE)\(\s*[\"$/]|method\s*=\s*[\"'](POST|PUT|PATCH|DELETE)")),
]
CONTRACT_GLOBS = ["**/openapi*.y*ml", "**/openapi*.json", "**/*.proto", "**/*.avsc",
                  "**/schemas/**/*.json", "**/*.schema.json", "**/asyncapi*.y*ml", "**/*.graphql",
                  "**/migrations/**/*.cql", "**/migrations/**/*.sql", "**/db/migration/**/*.sql", "**/*.cql"]
HEAVY_TEST = re.compile(r"@MicronautTest|@SpringBootTest|Testcontainers|GenericContainer|PostgreSQLContainer|"
                        r"@Testcontainers|EmbeddedServer|ApplicationContext\.run|docker|@QuarkusTest")
MOCK = re.compile(r"\bmockk\b|\bmockk<|\bevery\s*\{|Mockito|@MockBean|@Mock\b|\bmock\(|unittest\.mock|\bpatch\(|jest\.mock")


CLASS_HDR = re.compile(r"\b(class|object|interface)\s+(\w+)")


def supertypes(text: str) -> list[tuple[str, list[str]]]:
    """(declared name, supertypes) for each Kotlin/Java class header, multi-line safe."""
    out = []
    for m in CLASS_HDR.finditer(text):
        i, n = m.end(), len(text)
        depth_angle = 0
        while i < n and (text[i] in " \t" or text[i] == "<" or depth_angle):
            depth_angle += text[i] == "<"
            depth_angle -= text[i] == ">"
            i += 1
        while i < n and text[i] in " \t\n" and text[i:i + 1] != "(":
            if text[i] == "\n" and text[i + 1:i + 2].strip():
                break
            i += 1
        if i < n and text[i] == "(":  # constructor
            depth = 0
            while i < n:
                depth += text[i] == "("
                depth -= text[i] == ")"
                i += 1
                if depth == 0:
                    break
        j = i
        while j < n and text[j] in " \t\n":
            j += 1
        sup = ""
        if text[j:j + 1] == ":":
            k, depth = j + 1, 0
            while k < n:
                ch = text[k]
                depth += ch in "(<"
                depth -= ch in ")>"
                if depth <= 0 and (ch == "{" or (ch == "\n" and not text[j + 1:k].rstrip().endswith(","))):
                    break
                k += 1
            sup = text[j + 1:k]
        elif text[j:j + 10].startswith(("implements", "extends")):
            k = text.find("{", j)
            sup = text[j:k if k != -1 else n]
        names = re.findall(r"\b([A-Z]\w*)\b(?:\s*<[^>]*>)?\s*(?:\(|,|$|\{|by\b)", sup + ",")
        out.append((m.group(2), [x for x in names if x not in ("implements", "extends")]))
    return out


FUN_RE = re.compile(r"\b(?:fun|def|function)\s+(?:<[^>]+>\s*)?(\w+)\s*[(<]")


def enclosing_writer(src: str, line_no: int) -> str:
    """Name of the function that performs the write at `line_no`.

    If the write text sits in a property (e.g. a prepared statement `by lazy`), return
    the function in the same file that uses that property.
    """
    lines = src.splitlines()
    before = lines[:line_no]
    for ln in reversed(before):
        fm = FUN_RE.search(ln)
        pm = re.search(r"\b(?:val|var)\s+(\w+)", ln)
        if fm:
            return fm.group(1)
        if pm and not ln.startswith(" " * 12):
            prop = pm.group(1)
            funs = [(m.start(), m.group(1)) for m in FUN_RE.finditer(src)]
            for idx, (pos, name) in enumerate(funs):
                end = funs[idx + 1][0] if idx + 1 < len(funs) else len(src)
                if re.search(rf"\b{re.escape(prop)}\b", src[pos:end]):
                    return name
            return prop
    return ""


def lang_of(p: Path) -> str:
    return scar_scan.CODE_EXT.get(p.suffix, "other")


def strip_comments(text: str) -> str:
    text = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    return "\n".join(ln for ln in text.splitlines() if ln.strip() and not re.match(r"^\s*(//|#)", ln))


def git_files(root: Path) -> list[Path]:
    out = scar_scan.run(["git", "ls-files"], root)
    return [root / f for f in out.splitlines()]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    a = ap.parse_args()

    targets = [Path(p).resolve() for p in a.paths]
    root = scar_scan.git_root(targets[0])
    if root is None:
        print("error: not inside a git repository", file=sys.stderr)
        return 2
    head = scar_scan.run(["git", "rev-parse", "--short", "HEAD"], root).strip()

    def in_target(p: Path) -> bool:
        return any(p == t or t in p.parents for t in targets)

    all_files = [p for p in git_files(root) if p.suffix in SRC_EXT and p.exists()]
    main_files = [p for p in all_files if not scar_scan.TEST_HINT.search(str(p.relative_to(root)))]
    test_files = [p for p in all_files if scar_scan.TEST_HINT.search(str(p.relative_to(root)))]
    tgt = [p for p in main_files if in_target(p)]
    if not tgt:
        print("error: no tracked non-test source files under target", file=sys.stderr)
        return 2
    text = {p: p.read_text(encoding="utf-8", errors="replace") for p in all_files}
    rel = lambda p: str(p.relative_to(root))  # noqa: E731

    # --- size and concepts
    loc = {rel(p): len(strip_comments(text[p]).splitlines()) for p in tgt}
    decls: list[dict] = []
    for p in tgt:
        lg = lang_of(p)
        for m in DECL.get(lg, DECL["kotlin"]).finditer(text[p]):
            decls.append({"name": m.group(2), "kind": m.group(1), "file": rel(p)})
        if lg in TOP_FUN:
            for m in TOP_FUN[lg].finditer(text[p]):
                decls.append({"name": m.group(1), "kind": "top-level fun", "file": rel(p)})
    pkgs = sorted({m.group(1) for p in tgt for m in PKG.finditer(text[p])})

    # --- usage counts (outside the declaring file), split main/test
    for d in decls:
        w = re.compile(rf"\b{re.escape(d['name'])}\b")
        d["refs_main_outside_file"] = sum(1 for p in main_files if rel(p) != d["file"] and w.search(text[p]))
        d["refs_test_files"] = sum(1 for p in test_files if w.search(text[p]))
    # interfaces: implementations
    sup_by_file = {p: supertypes(text[p]) for p in all_files if p.suffix in (".kt", ".java", ".scala")}
    for d in decls:
        if d["kind"] != "interface":
            continue
        nm = d["name"]
        anon = re.compile(rf"object\s*:\s*{re.escape(nm)}\b")

        def impls(files):
            hits = []
            for p in files:
                for cname, sups in sup_by_file.get(p, []):
                    if nm in sups and cname != nm:
                        hits.append(f"{cname} ({p.name})")
                hits += [f"<anonymous> ({p.name})"] * len(anon.findall(text[p]))
            return sorted(hits)
        d["impls_main"], d["impls_test"] = impls(main_files), impls(test_files)

    # --- imports out / fan-in
    base = ".".join(pkgs[0].split(".")[:3]) if pkgs else ""
    out_internal, out_external = Counter(), Counter()
    for p in tgt:
        for m in IMPORT.finditer(text[p]):
            imp = m.group(1)
            pkg = ".".join(imp.split(".")[:-1]) or imp
            if base and imp.startswith(base):
                if not any(pkg == t or pkg.startswith(t + ".") for t in pkgs):
                    out_internal[pkg] += 1
            else:
                out_external[".".join(imp.split(".")[:3])] += 1
    fan_in = defaultdict(set)
    for p in main_files:
        if in_target(p):
            continue
        for m in IMPORT.finditer(text[p]):
            if any(m.group(1).startswith(t + ".") for t in pkgs):
                src_pkg = PKG.search(text[p])
                fan_in[src_pkg.group(1) if src_pkg else rel(p)].add(m.group(1).split(".")[-1])

    # --- write sites, and who else writes the same names
    writes = []
    for p in tgt:
        for i, line in enumerate(text[p].splitlines(), 1):
            for kind, rx in WRITE_PATTERNS:
                m = rx.search(line)
                if m:
                    name = ""
                    if kind in ("sql-write", "ddl"):
                        name = next((g for g in reversed(m.groups()) if g and re.match(r"^[\w.]+$", g)
                                     and g.lower() not in {"table", "index", "exists", "set"}), "")
                    fn = enclosing_writer(text[p], i)
                    writes.append({"file": rel(p), "line": i, "kind": kind, "target": name,
                                   "in_function": fn, "code": line.strip()[:140]})
                    break
    shared_writers = {}
    for w in writes:
        t = w["target"]
        if w["kind"] not in ("sql-write", "ddl") or not t or len(t) < 3:
            continue
        rx = re.compile(rf"(?i)(insert\s+into|update|delete\s+from|merge\s+into|upsert\s+into)\s+[\"`]?{re.escape(t)}\b")
        others = sorted({rel(p) for p in main_files if not in_target(p) and rx.search(text[p])})
        if others:
            shared_writers[t] = others

    # who outside the target calls the functions that write
    write_fns = sorted({w["in_function"] for w in writes if w["in_function"] and w["kind"] != "outbound-mutation"})
    write_callers = {}
    for fn in write_fns:
        rx = re.compile(rf"\.{re.escape(fn)}\s*\(")
        callers = sorted({rel(p) for p in main_files if not in_target(p) and rx.search(text[p])})
        if callers:
            write_callers[fn] = callers

    # --- contracts
    contracts = sorted({str(f.relative_to(root)) for g in CONTRACT_GLOBS for f in root.glob(g)
                        if not any(s in f.parts for s in scar_scan.SKIP_DIRS)})[:40]
    versioned_paths = sorted({m.group(0) for p in tgt for m in re.finditer(r"[\"'](/[\w{}/.-]*?/v\d+[\w{}/.-]*)[\"']", text[p])})[:20]

    # --- tests that exercise the target
    names = {d["name"] for d in decls if len(d["name"]) > 3}
    tests = []
    pkg_rx = re.compile(r"^\s*(?:import|package)\s+(" + "|".join(re.escape(k) for k in pkgs) + r")\b", re.M) if pkgs else None
    for p in test_files:
        if pkg_rx is not None and not pkg_rx.search(text[p]):
            continue  # must import or share the target package
        hits = [n for n in names if re.search(rf"\b{re.escape(n)}\b", text[p])]
        if not hits:
            continue
        t = text[p]
        tests.append({"file": rel(p), "refs": sorted(hits)[:8],
                      "heavy": bool(HEAVY_TEST.search(t)), "mocks": len(MOCK.findall(t)),
                      "property": bool(re.search(r"checkAll|forAll|\bArb\.|@given|fc\.assert|@Property", t))})

    # --- scar candidates (no git tracing)
    scars = scar_scan.scan(targets, root, include_tests=False)
    scar_cats = Counter(c for s in scars for c in s.categories)

    # --- build deps
    build = next((d / n for d in [targets[0], *targets[0].parents] for n in ("build.gradle.kts", "build.gradle", "pom.xml", "pyproject.toml", "package.json")
                  if (d / n).exists() and (d == root or root in d.parents)), None)
    deps = []
    if build:
        bt = build.read_text(encoding="utf-8", errors="replace")
        deps = re.findall(r"^\s*(?:implementation|api|compileOnly|runtimeOnly|kapt|ksp)\s*\(\s*[\"']([^\"']+)", bt, re.M)

    facts = {
        "repo": root.name, "head": head, "targets": [rel(t) if t != root else "." for t in targets],
        "packages": pkgs, "files": len(tgt), "loc_total": sum(loc.values()),
        "loc_by_file": dict(sorted(loc.items(), key=lambda kv: -kv[1])[:15]),
        "declarations": decls,
        "imports_out_internal": dict(out_internal.most_common(25)),
        "imports_out_external": dict(out_external.most_common(25)),
        "fan_in": {k: sorted(v) for k, v in sorted(fan_in.items())},
        "write_sites": writes[:60], "shared_writers": shared_writers, "write_api_callers": write_callers,
        "contract_artifacts": contracts, "versioned_paths": versioned_paths,
        "tests": tests, "scar_candidates": len(scars), "scar_categories": dict(scar_cats),
        "build_file": rel(build) if build else None, "build_deps": deps,
    }
    print(json.dumps(facts, indent=2) if a.format == "json" else to_md(facts))
    return 0


def to_md(f: dict) -> str:
    o = [f"# module_facts: {', '.join(f['targets'])} ({f['repo']} @ {f['head']})", "",
         f"- packages: {', '.join(f['packages']) or 'none'}",
         f"- files: {f['files']}, LOC (non-blank, non-comment): {f['loc_total']}",
         f"- scar candidates: {f['scar_candidates']} {f['scar_categories']}",
         f"- build deps declared ({f['build_file']}): {len(f['build_deps'])}", "",
         "## Largest files", ""] + [f"- {k}: {v}" for k, v in f["loc_by_file"].items()]
    o += ["", "## Declarations", "", "| name | kind | file | refs in other main files | test files | impls (main/test) |",
          "|---|---|---|---|---|---|"]
    for d in f["declarations"]:
        impl = (f"{len(d['impls_main'])}/{len(d['impls_test'])}: " + ", ".join(d["impls_main"][:3])) if "impls_main" in d else ""
        o.append(f"| {d['name']} | {d['kind']} | {Path(d['file']).name} | {d['refs_main_outside_file']} | {d['refs_test_files']} | {impl} |")
    o += ["", "## Imports out (internal packages)", ""] + [f"- {k}: {v}" for k, v in f["imports_out_internal"].items()]
    o += ["", "## Imports out (external, top 25)", ""] + [f"- {k}: {v}" for k, v in f["imports_out_external"].items()]
    o += ["", "## Fan-in (packages importing the target)", ""] + [f"- {k}: {', '.join(v)}" for k, v in f["fan_in"].items()]
    o += ["", "## Write sites", ""] + [f"- {w['kind']} `{w['target'] or '-'}` in `{w['in_function'] or '?'}` {w['file']}:{w['line']} `{w['code'][:90]}`" for w in f["write_sites"]]
    o += ["", "## Shared writers (same table written outside target)", ""] + \
         ([f"- {t}: {', '.join(v)}" for t, v in f["shared_writers"].items()] or ["- none found"])
    o += ["", "## Callers of the target's write functions (outside target)", ""] + \
         ([f"- {t}: {', '.join(v)}" for t, v in f["write_api_callers"].items()] or ["- none found"])
    o += ["", "## Contract artifacts in repo", ""] + ([f"- {c}" for c in f["contract_artifacts"]] or ["- none found"])
    o += ["", "## Versioned paths in target", ""] + ([f"- {c}" for c in f["versioned_paths"]] or ["- none found"])
    o += ["", "## Tests referencing target", "", "| file | heavy (app/container) | mock calls | property | refs |", "|---|---|---|---|---|"]
    for t in f["tests"]:
        o.append(f"| {t['file']} | {'yes' if t['heavy'] else 'no'} | {t['mocks']} | {'yes' if t['property'] else 'no'} | {', '.join(t['refs'])} |")
    return "\n".join(o)


if __name__ == "__main__":
    sys.exit(main())
