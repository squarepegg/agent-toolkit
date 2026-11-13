#!/usr/bin/env python3
"""Mutation check of the self-test: plant one bug at a time in a copy of scripts/, run the test that should catch it,
and require an assertion FAILURE in that test (an error/crash is WRONG REASON, not a catch). The unmutated suite
must pass first. Mutants that are not valid Python are rejected. Exit 0 only when every mutant is caught.
  python3 tests/planted_bugs.py [ONLY_SUBSTRING]   (PAR=<n> parallel, default 6)"""
import concurrent.futures as cf, os, py_compile, shutil, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "..", "scripts")
TEST = os.path.join(HERE, "test_spec_first.py")

# (name, file, old, new, test that must fail)
MUTANTS = [
    ("lint ignores missing sections", "spec.py", 'if s not in secs: errs.append(f"missing section', 'if False: errs.append(f"missing section', "Lint.test_lint_missing_section"),
    ("decision tag optional", "spec.py", r'\((confirmed|assumed)\)\s*$")', r'(?:\((confirmed|assumed)\))?\s*$")', "Lint.test_lint_decision_needs_tag"),
    ("exit clause optional", "spec.py", 'if not any(c[0] == "exit" for c in clauses): err(', 'if False: err(', "Lint.test_lint_check_needs_exit_clause"),
    ("unknown cover id allowed", "spec.py", 'if x not in ids: errs.append(', 'if False: errs.append(', "Lint.test_lint_unknown_cover_id"),
    ("uncovered never an error", "spec.py", '(errs if accepted else warns).append(f"{x} is not covered', 'warns.append(f"{x} is not covered', "Lint.test_lint_uncovered_is_error_when_accepted"),
    ("open questions ignored", "spec.py", 'if oq and not all(l in EMPTY for l in oq)', 'if False', "Lint.test_lint_open_questions_block_accepted"),
    ("placeholders ignored", "spec.py", 'if PLACEHOLDER.search(l): errs', 'if False: errs', "Lint.test_lint_placeholder_blocks_accepted"),
    ("deviations allowed at accept", "spec.py", 'if dev: errs.append', 'if False: errs.append', "Lint.test_lint_deviations_must_be_empty_on_accept"),
    ("no impl warning", "spec.py", 'if "`" in t or IMPL_WORDS.search(t):', 'if False:', "Lint.test_lint_warns_on_implementation_words"),
    ("duplicate ids allowed", "spec.py", 'if rid in spec[key]: err(', 'if False: err(', "Lint.test_lint_duplicate_id"),
    ("hash skips Checks", "spec.py", 'NORMATIVE = ["Goal", "Decisions", "Requirements", "Edge cases", "Out of scope", "Constraints", "Checks"]',
     'NORMATIVE = ["Goal", "Decisions", "Requirements", "Edge cases", "Out of scope", "Constraints"]', "Accept.test_lock_catches_check_edit"),
    ("hash covers Deviations", "spec.py", '"Constraints", "Checks"]\nDEFAULT', '"Constraints", "Checks", "Deviations"]\nDEFAULT', "Accept.test_lock_allows_deviations_and_whitespace"),
    ("lock never checked", "spec.py", 'if lock.get("hash") != normative_hash(spec): return', 'if False: return', "Accept.test_lock_catches_requirement_edit"),
    ("accept ignores lint", "spec.py", "    if errs:\n        for e in errs: print(f\"error: {e}\")\n        return 1\n    text = read(path)", "    text = read(path)", "Accept.test_accept_refuses_open_questions"),
    ("exit code not compared", "evidence.py", 'ok = rc == val;', 'ok = True;', "Evidence.test_evidence_exit_code_compared"),
    ("contains always true", "evidence.py", 'ok = val in text; desc', 'ok = True; desc', "Evidence.test_evidence_failing_clause"),
    ("lacks inverted", "evidence.py", 'ok = val not in text; desc', 'ok = val in text; desc', "Evidence.test_evidence_lacks_and_matches_clauses"),
    ("manual counted as pass", "evidence.py", 'row.update(status="NOT CHECKED", reason="manual check', 'row.update(status="PASS", reason="manual check', "Evidence.test_evidence_manual_is_not_checked"),
    ("timeout counted as pass", "evidence.py", 'return {"status": "NOT CHECKED", "reason": f"timed out', 'return {"status": "PASS", "reason": f"timed out', "Evidence.test_evidence_timeout_is_not_checked"),
    ("missing command is a plain fail", "evidence.py", 'if p.returncode == 127 and "command not found" in p.stderr:', 'if False:', "Evidence.test_evidence_missing_command_is_not_checked"),
    ("modified test not guarded", "evidence.py", 'if st in ("M", "D") and', 'if st in ("D",) and', "Evidence.test_evidence_guard_existing_test_modified"),
    ("deleted test not guarded", "evidence.py", 'if st in ("M", "D") and', 'if st in ("M",) and', "Evidence.test_evidence_guard_existing_test_deleted"),
    ("added tests guarded", "evidence.py", 'if st in ("M", "D") and', 'if st in ("M", "D", "A") and', "Evidence.test_evidence_new_test_is_not_a_guard"),
    ("spec lock not guarded", "evidence.py", 'if lock_reason: guards.append', 'if False: guards.append', "Evidence.test_evidence_guard_spec_changed"),
    ("out dir inside repo allowed", "evidence.py", 'if out == root or out.startswith(root + os.sep):', 'if False:', "Evidence.test_evidence_out_inside_repo_refused"),
    ("untracked files dropped", "evidence.py", 'files[path] = "A"', 'pass', "Evidence.test_evidence_untracked_files_in_diff"),
    ("clock in evidence", "evidence.py", 'data = {"diff_sha"', 'import time; data = {"t": time.time(), "diff_sha"', "Evidence.test_evidence_is_deterministic"),
    ("custom globs ignored", "spec.py", 'if m: spec["test_globs"] = ', 'if False: spec["test_globs"] = ', "Evidence.test_evidence_custom_test_globs"),
    ("failed check not enforced", "verdict.py", 'if failed: return "FAIL"', 'if False: return "FAIL"', "Verdict.test_verdict_pass_on_failed_check_is_fail"),
    ("unrun check not enforced", "verdict.py", 'if unrun: return "NOT CHECKED"', 'if False: return "NOT CHECKED"', "Verdict.test_verdict_manual_check_keeps_not_checked"),
    ("uncited pass accepted", "verdict.py", 'if not good: return "UNSUPPORTED"', 'if False: return "UNSUPPORTED"', "Verdict.test_verdict_rubber_stamp_is_unsupported"),
    ("cover not required", "verdict.py", 'elif rid not in c["covers"]: bad.append', 'elif False: bad.append', "Verdict.test_verdict_check_must_cover_id"),
    ("line range unchecked", "verdict.py", 'if not (1 <= a <= b <= n):', 'if False:', "Verdict.test_verdict_line_out_of_range"),
    ("any file is evidence", "verdict.py", 'if path not in data["files"] or data["files"][path] == "D":', 'if False:', "Verdict.test_verdict_untouched_file_not_evidence"),
    ("edge needs no test", "verdict.py", 'if rid.startswith("E") and not EV.is_test(', 'if False and not EV.is_test(', "Verdict.test_verdict_edge_case_needs_test_file"),
    ("missing row is pass", "verdict.py", 'return "MISSING", "the report has no row for it"', 'return "PASS", "the report has no row for it"', "Verdict.test_verdict_missing_row"),
    ("duplicate rows allowed", "verdict.py", 'if rid in rows: errs.append', 'if False: errs.append', "Verdict.test_verdict_duplicate_row_is_bad_input"),
    ("reviewer fail loosened", "verdict.py", 'if verdict == "FAIL": return "FAIL"', 'if False: return "FAIL"', "Verdict.test_verdict_reviewer_fail_is_kept"),
    ("guards ignored", "verdict.py", 'blocks = list(data["guards"])', 'blocks = []', "Verdict.test_verdict_guard_blocks_done"),
    ("stale tree ignored", "verdict.py", 'if hashlib.sha256(patch.encode()).hexdigest() != data["diff_sha"]:', 'if False:', "Verdict.test_verdict_stale_tree_blocks_done"),
    ("stale spec ignored", "verdict.py", 'if S.normative_hash(spec) != data["spec_hash"]: blocks', 'if False: blocks', "Verdict.test_verdict_stale_spec_blocks_done"),
    ("reviewer edit allowed", "review.sh", '[ "$before" = "$after" ] ||', 'true ||', "Review.test_review_reviewer_edit_voids_report"),
    ("no round limit", "review.sh", '[ "$n" -le "$MAX" ] ||', 'true ||', "Review.test_review_round_limit"),
    ("brief leaks nothing but drops diff", "review.sh", "  cat \"$DIR/diff.patch\"\n", "", "Review.test_review_brief_holds_only_spec_evidence_diff"),
]


def run_test(scripts, test):
    env = dict(os.environ, SPEC_FIRST_SCRIPTS=scripts)
    p = subprocess.run([sys.executable, TEST, test], capture_output=True, text=True, env=env, timeout=600)
    out = p.stdout + p.stderr
    if p.returncode == 0: return "MISSED", ""
    if "errors=" in out.splitlines()[-1] or f"ERROR: {test.split('.')[1]}" in out: return "WRONG REASON (error, not an assertion)", out[-1500:]
    return "caught", ""


def one(m):
    name, fn, old, new, test = m
    d = tempfile.mkdtemp(prefix="sf-mut-")
    try:
        sd = os.path.join(d, "scripts"); shutil.copytree(SRC, sd)
        os.symlink(os.path.join(HERE, "..", "templates"), os.path.join(d, "templates"))
        p = os.path.join(sd, fn); s = open(p).read()
        if s.count(old) != 1: return name, f"PATTERN {'NOT FOUND' if not s.count(old) else 'AMBIGUOUS'}", ""
        open(p, "w").write(s.replace(old, new, 1))
        if fn.endswith(".py"):
            try: py_compile.compile(p, doraise=True)
            except py_compile.PyCompileError: return name, "INVALID MUTANT", ""
        r, detail = run_test(sd, test)
        return name, r, detail
    finally:
        shutil.rmtree(d, ignore_errors=True)


def main():
    only = sys.argv[1] if len(sys.argv) > 1 else ""
    p = subprocess.run([sys.executable, TEST], capture_output=True, text=True)
    if p.returncode != 0: print("planted-bugs: the unmutated self-test fails; fix it first\n" + p.stderr[-2000:]); return 2
    todo = [m for m in MUTANTS if only in m[0]]
    with cf.ThreadPoolExecutor(int(os.environ.get("PAR", "6"))) as ex:
        res = list(ex.map(one, todo))
    bad = 0
    for name, r, detail in res:
        print(f"{r}: {name}")
        if r != "caught": bad += 1; print(detail) if detail else None
    print(f"planted-bugs: {len(res) - bad}/{len(res)} caught")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
