#!/usr/bin/env python3
"""Mutation-check drafted tests: plant one bug at a time and see which tests catch it.

A mutant is one exact text replacement in one source file. For each mutant the script
applies it, runs the test command, reads the JUnit XML results, and restores the file.

Usage:
  mutate.py MUTANTS.json --results GLOB [--results GLOB ...] [--watch REGEX]
            [--timeout SECONDS] [--no-baseline] [--format md|json] -- COMMAND ...

MUTANTS.json is a list of objects:
  {"name": "F1 cooldown boundary < to <=",
   "file": "path/to/CooldownFilter.kt",
   "old":  "return now < lastReturned.plus(window)",
   "new":  "return now <= lastReturned.plus(window)"}

Results per mutant:
  killed     at least one test failed: the suite catches this bug
  survived   the command passed: no test catches this bug
  no-build   the command failed but no test failed (compile error or timeout): not a kill,
             fix the mutant

--watch REGEX lists the matching tests (e.g. 'Properties') that killed no mutant. A drafted
test that kills nothing may be vacuous: write a mutant aimed at the bug it exists to catch.

Safety: every "old" must occur exactly once in its file, or nothing is changed. The
unmutated suite must pass first (skip with --no-baseline). Files are restored after each
run, including on timeout and Ctrl-C. Stdlib only. Python 3.9+.
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import signal
import subprocess
import sys
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Mutant:
    name: str
    file: Path
    old: str
    new: str


@dataclass
class Outcome:
    mutant: str
    result: str
    exit_code: int | None
    killed_by: list[str] = field(default_factory=list)


def load_mutants(path: Path) -> list[Mutant]:
    try:
        raw = json.loads(path.read_text())
        mutants = [Mutant(m["name"], Path(m["file"]), m["old"], m["new"]) for m in raw]
    except (OSError, ValueError, KeyError, TypeError) as e:
        sys.exit(f"cannot read mutants from {path}: {e!r} (need a JSON list of name/file/old/new)")
    problems = []
    for m in mutants:
        if not m.file.is_file():
            problems.append(f"{m.name}: file not found: {m.file}")
            continue
        count = m.file.read_text().count(m.old)
        if count != 1:
            problems.append(f"{m.name}: 'old' occurs {count} times in {m.file} (need exactly 1)")
        if m.old == m.new:
            problems.append(f"{m.name}: 'old' and 'new' are identical")
    if problems:
        sys.exit("mutants rejected, nothing changed:\n  " + "\n  ".join(problems))
    return mutants


def result_files(globs: list[str]) -> list[Path]:
    return sorted({Path(p) for g in globs for p in glob.glob(g, recursive=True)})


def junit_root(path: Path) -> ET.Element | None:
    """Parse a JUnit XML file written by the local build. Files with a DTD are refused, which
    blocks entity-expansion and external-entity attacks; JUnit output never declares one."""
    text = path.read_text(errors="replace")
    if "<!DOCTYPE" in text or "<!ENTITY" in text:
        print(f"skipping {path}: JUnit results must not declare a DTD", file=sys.stderr)
        return None
    try:
        return ET.fromstring(text)  # noqa: S314  # nosec B314 - local build output, DTDs refused above
    except ET.ParseError:
        return None


def failed_tests(globs: list[str]) -> list[str]:
    failed = []
    for f in result_files(globs):
        root = junit_root(f)
        if root is None:
            continue
        for case in root.iter("testcase"):
            if case.find("failure") is not None or case.find("error") is not None:
                cls = (case.get("classname") or root.get("name") or "").split(".")[-1]
                failed.append(f"{cls} › {case.get('name')}")
    return sorted(set(failed))


def all_tests(globs: list[str]) -> list[str]:
    names = []
    for f in result_files(globs):
        root = junit_root(f)
        if root is None:
            continue
        for case in root.iter("testcase"):
            cls = (case.get("classname") or root.get("name") or "").split(".")[-1]
            names.append(f"{cls} › {case.get('name')}")
    return sorted(set(names))


def run(command: list[str], globs: list[str], timeout: int | None) -> int | None:
    for f in result_files(globs):
        f.unlink()
    proc = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        return proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait()
        return None


def check(mutant: Mutant, command: list[str], globs: list[str], timeout: int | None) -> Outcome:
    original = mutant.file.read_text()
    try:
        mutant.file.write_text(original.replace(mutant.old, mutant.new, 1))
        code = run(command, globs, timeout)
    finally:
        mutant.file.write_text(original)
    failed = failed_tests(globs)
    if failed:
        return Outcome(mutant.name, "killed", code, failed)
    if code == 0:
        return Outcome(mutant.name, "survived", code)
    return Outcome(mutant.name, "no-build", code)


def main() -> None:
    if "--" not in sys.argv:
        sys.exit("usage: mutate.py MUTANTS.json --results GLOB [...] -- COMMAND ...")
    split = sys.argv.index("--")
    command = sys.argv[split + 1 :]
    parser = argparse.ArgumentParser(description="Mutation-check drafted tests: plant one bug at a time.")
    parser.add_argument("mutants", type=Path)
    parser.add_argument("--results", action="append", required=True, help="JUnit XML glob, e.g. 'build/test-results/test/*.xml'")
    parser.add_argument("--watch", help="regex: list matching tests that killed no mutant")
    parser.add_argument("--timeout", type=int, help="seconds per run")
    parser.add_argument("--no-baseline", action="store_true")
    parser.add_argument("--format", choices=["md", "json"], default="md")
    args = parser.parse_args(sys.argv[1:split])
    if not command:
        sys.exit("no test command after --")

    mutants = load_mutants(args.mutants)
    signal.signal(signal.SIGTERM, lambda *_: (_ for _ in ()).throw(KeyboardInterrupt()))

    observed: list[str] = []
    if not args.no_baseline:
        code = run(command, args.results, args.timeout)
        if code != 0 or failed_tests(args.results):
            sys.exit(f"baseline failed (exit {code}): the unmutated suite must pass first")
        observed = all_tests(args.results)

    outcomes = []
    for m in mutants:
        print(f"... {m.name}", file=sys.stderr)
        outcomes.append(check(m, command, args.results, args.timeout))

    watched: list[str] = []
    if args.watch:
        import re

        pattern = re.compile(args.watch)
        killers = {t for o in outcomes for t in o.killed_by}
        watched = [t for t in observed if pattern.search(t) and t not in killers]

    if args.format == "json":
        print(json.dumps({"outcomes": [o.__dict__ for o in outcomes], "watched_killed_nothing": watched}, indent=2))
        return
    killed = sum(o.result == "killed" for o in outcomes)
    print(f"{killed} of {len(outcomes)} mutants killed\n")
    print("| mutant | result | killed by |\n|---|---|---|")
    for o in outcomes:
        print(f"| {o.mutant} | {o.result} | {'; '.join(o.killed_by) or '-'} |")
    if args.watch:
        print(f"\nWatched tests ({args.watch}) that killed no mutant: " + (", ".join(watched) or "none"))
        if not observed:
            print("(no baseline run, so watched tests are unknown)")


if __name__ == "__main__":
    main()
