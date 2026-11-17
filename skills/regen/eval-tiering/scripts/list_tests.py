#!/usr/bin/env python3
"""Inventory tests and live signals for eval-tiering.

For each test file: framework, test case names, and coupling signals (mocks, interaction
verification, internal/reflective access) versus boundary signals (HTTP against a server,
schemas, golden files, property tests). Suggests a tier per file and answers the
rewrite question per file. Also lists live evaluations in the repo (monitors, SLOs, k6
offers, metrics emitted).

Usage:
  list_tests.py PATH [PATH ...] [--for MAIN_PATH] [--format md|json]

PATH: test files or directories. With --for, only tests that import a package declared
under MAIN_PATH are kept, and the whole repo is searched for them.

Stdlib only. Python 3.9+. Read-only. The tier is a suggestion; the skill decides.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from pathlib import Path


def _load_shared(name: str):
    """Load a module from ../../_shared/scripts by path (skills are not a package)."""
    path = Path(__file__).resolve().parents[2] / "_shared" / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod  # dataclasses need the module registered
    spec.loader.exec_module(mod)
    return mod


scar_scan = _load_shared("scar_scan")

TEST_EXT = {".kt", ".java", ".py", ".ts", ".tsx", ".js", ".go"}

FRAMEWORK = [
    ("kotest", re.compile(r":\s*(FunSpec|StringSpec|BehaviorSpec|DescribeSpec|ShouldSpec|FreeSpec|WordSpec|FeatureSpec|ExpectSpec|AnnotationSpec)\s*\(")),
    ("junit5", re.compile(r"org\.junit\.jupiter")),
    ("junit4", re.compile(r"org\.junit\.Test\b")),
    ("pytest", re.compile(r"^def test_|^\s+def test_|import pytest", re.M)),
    ("jest/vitest", re.compile(r"\b(describe|it|test)\(\s*['\"`]")),
    ("go-test", re.compile(r"func Test\w+\(t \*testing\.T\)")),
]
CASE = [
    re.compile(r"fun\s+`([^`]+)`"),
    re.compile(r"\b(?:test|should|it|context|describe|scenario|expect)\(\s*\"([^\"]+)\""),
    re.compile(r"^\s*\"([^\"]{6,})\"\s*\{", re.M),
    re.compile(r"def (test_\w+)"),
    re.compile(r"\b(?:it|test)\(\s*['`]([^'`]+)['`]"),
    re.compile(r"func (Test\w+)\("),
    re.compile(r"@Test\s+(?:fun|void|public void)\s+(\w+)"),
]
SIGNALS = {
    "mocks": re.compile(r"\bmockk\b|\bmockk<|\bevery\s*\{|\bcoEvery\b|Mockito|@MockBean|@Mock\b|\bmock\(|unittest\.mock|\bpatch\(|jest\.(mock|fn)|vi\.(mock|fn)"),
    "verify": re.compile(r"\b(co)?verify\s*[({]|\bverify\(|assert_called|toHaveBeenCalled|\.times\(\d"),
    "fakes": re.compile(r"object\s*:\s*[A-Z]\w+|class\s+(Fake|Stub|InMemory)\w*|\bFake[A-Z]\w*\("),
    "reflection": re.compile(r"isAccessible\s*=\s*true|getDeclared(Field|Method)|ReflectionTestUtils|@VisibleForTesting|setAccessible\(true\)|_[a-z]\w*\s*=\s*"),
    "http_boundary": re.compile(r"@Client\(|toBlocking\(\)\.exchange|\.exchange\(|MockWebServer|WireMock|EmbeddedServer|TestRestTemplate|WebTestClient|supertest|requests\.(get|post)|httpx\.|RestAssured"),
    "containers": re.compile(r"Testcontainers|GenericContainer|\w+Container\(|@Container\b|docker"),
    "app_context": re.compile(r"@MicronautTest|@SpringBootTest|ApplicationContext\.run|@QuarkusTest|startKoin"),
    "property": re.compile(r"\bcheckAll\b|\bforAll\b|\bArb\.|net\.jqwik|@given\(|from hypothesis|fc\.assert|fc\.property"),
    "schema_contract": re.compile(r"(?i)json-?schema|schemaValidator|SchemaRegistry|openapi|\bpact\b|\.avsc|\.proto\b|ContractTest|@Contract"),
    "golden_files": re.compile(r"getResource(AsStream)?\(|src/test/resources|\bgolden\b|approval|toMatchSnapshot|\.json\"\)"),
    "time_control": re.compile(r"Clock\.fixed|TestCoroutineScheduler|advanceTimeBy|freeze_?time|useFakeTimers"),
}
LIVE = [
    ("datadog-entity", re.compile(r"entity\.datadog\.ya?ml$")),
    ("monitor/slo (terraform)", re.compile(r"\.tf$")),
    ("alert rules", re.compile(r"(alert|rules?|monitor)s?[^/]*\.ya?ml$", re.I)),
    ("k6", re.compile(r"(^|/)k6/|\.k6\.js$|load[-_]?test", re.I)),
]
LIVE_CONTENT = re.compile(r"datadog_monitor|datadog_service_level_objective|\bslo\b|offers\s*:\s*\{|\balert\s*:|query\s*:\s*['\"]?(avg|sum|max|p\d\d)", re.I)
METRIC = re.compile(r"""(?:counter|timer|gauge|summary|histogram|distributionSummary)\(\s*["']([\w.\-]+)["']""")


def code_only(src: str) -> str:
    """Drop comments so prose like 'contract' does not count as a signal."""
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    src = re.sub(r"(^|\s)//.*$", r"\1", src, flags=re.M)
    return src


def internal_names(root: Path, files: list[Path]) -> set[str]:
    names = set()
    for p in files:
        if p.suffix != ".kt":
            continue
        for m in re.finditer(r"^\s*internal\s+(?:suspend\s+)?(?:fun|class|object|val)\s+(?:<[^>]+>\s*)?(\w+)",
                             p.read_text(encoding="utf-8", errors="replace"), re.M):
            names.add(m.group(1))
    return names


def classify(sig: dict, internal_hits: int) -> tuple[str, str, str]:
    coupled = sig["mocks"] + sig["verify"] + sig["reflection"] + internal_hits
    boundary = sig["http_boundary"] or sig["schema_contract"] or sig["golden_files"]
    reasons = []
    if sig["property"]:
        reasons.append("property-based")
    if boundary:
        reasons.append("asserts at a wire/data boundary")
    if coupled:
        reasons.append(f"implementation coupling: {sig['mocks']} mock, {sig['verify']} verify, "
                       f"{sig['reflection']} reflective, {internal_hits} internal-symbol refs")
    if (sig["property"] or boundary) and not coupled:
        tier = "durable"
        rewrite = "spec survives; harness must be ported" if not sig["http_boundary"] else "survives if run against the wire"
    elif coupled and not (sig["property"] or boundary):
        tier = "ephemeral"
        rewrite = "invalidated"
    elif coupled:
        tier = "mixed"
        rewrite = "partly: boundary cases survive, coupled cases do not"
    else:
        tier = "durable-candidate"
        rewrite = "partly: example-based checks on public API; restate as properties or wire contracts"
        reasons.append("example-based, no mocks")
    return tier, rewrite, "; ".join(reasons) or "no signals"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--for", dest="for_path")
    ap.add_argument("--format", choices=["md", "json"], default="md")
    a = ap.parse_args()

    paths = [Path(p).resolve() for p in a.paths]
    root = scar_scan.git_root(paths[0])
    if root is None:
        print("error: not inside a git repository", file=sys.stderr)
        return 2
    head = scar_scan.run(["git", "rev-parse", "--short", "HEAD"], root).strip()
    tracked = [root / f for f in scar_scan.run(["git", "ls-files"], root).splitlines()]
    main_src = [p for p in tracked if p.suffix in TEST_EXT and not scar_scan.TEST_HINT.search(str(p.relative_to(root)))]

    def under(p: Path, bases: list[Path]) -> bool:
        return any(p == b or b in p.parents for b in bases)

    candidates = [p for p in tracked if p.suffix in TEST_EXT and p.exists()
                  and scar_scan.TEST_HINT.search(str(p.relative_to(root)))]
    if a.for_path:
        fp = Path(a.for_path).resolve()
        pkgs = {m.group(1) for p in main_src if under(p, [fp])
                for m in re.finditer(r"^\s*package\s+([\w.]+)", p.read_text(encoding="utf-8", errors="replace"), re.M)}
        rx = re.compile(r"^\s*(?:import|package)\s+(" + "|".join(map(re.escape, pkgs)) + r")\b", re.M) if pkgs else None
        candidates = [p for p in candidates if rx and rx.search(p.read_text(encoding="utf-8", errors="replace"))]
    else:
        candidates = [p for p in candidates if under(p, paths)]

    internals = internal_names(root, main_src)
    files = []
    for p in sorted(candidates):
        t = p.read_text(encoding="utf-8", errors="replace")
        fw = next((n for n, rx in FRAMEWORK if rx.search(t)), "unknown")
        cases = []
        for rx in CASE:
            cases += [m.group(1) for m in rx.finditer(t)]
        cases = list(dict.fromkeys(c for c in cases if len(c) > 3))
        code = code_only(t)
        sig = {k: len(rx.findall(code)) for k, rx in SIGNALS.items()}
        ih = sum(1 for n in internals if len(n) > 5 and re.search(rf"\b{re.escape(n)}\b", code))
        tier, rewrite, why = classify(sig, ih)
        files.append({"file": str(p.relative_to(root)), "framework": fw, "cases": cases, "signals": sig,
                      "internal_refs": ih, "suggested_tier": tier, "language_rewrite": rewrite, "why": why,
                      "needs_app_or_container": bool(sig["app_context"] or sig["containers"])})

    live = []
    for p in tracked:
        r = str(p.relative_to(root))
        for kind, rx in LIVE:
            if rx.search(r) and p.exists():
                body = p.read_text(encoding="utf-8", errors="replace")
                if kind == "monitor/slo (terraform)" and not LIVE_CONTENT.search(body):
                    continue
                live.append({"kind": kind, "file": r})
                break
    metrics = sorted({m.group(1) for p in main_src for m in METRIC.finditer(p.read_text(encoding="utf-8", errors="replace"))})

    out = {"repo": root.name, "head": head, "test_files": files, "live": live, "metrics_emitted": metrics}
    if a.format == "json":
        print(json.dumps(out, indent=2))
        return 0
    o = [f"# list_tests: {root.name} @ {head}", "", f"{len(files)} test files, "
         f"{sum(len(f['cases']) for f in files)} cases", "",
         "| file | framework | cases | tier (suggested) | language rewrite | app/container | why |",
         "|---|---|---|---|---|---|---|"]
    for f in files:
        o.append(f"| {Path(f['file']).name} | {f['framework']} | {len(f['cases'])} | {f['suggested_tier']} | "
                 f"{f['language_rewrite']} | {'yes' if f['needs_app_or_container'] else 'no'} | {f['why']} |")
    o += ["", "## Cases", ""]
    for f in files:
        o.append(f"**{Path(f['file']).name}**")
        o += [f"- {c}" for c in f["cases"]]
        o.append("")
    o += ["## Live evaluations found", ""] + ([f"- {x['kind']}: {x['file']}" for x in live] or ["- none found in repo"])
    o += ["", "## Metrics emitted by main code", ""] + ([f"- {m}" for m in metrics] or ["- none found"])
    print("\n".join(o))
    return 0


if __name__ == "__main__":
    sys.exit(main())
