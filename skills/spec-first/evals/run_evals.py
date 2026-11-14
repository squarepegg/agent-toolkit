#!/usr/bin/env python3
"""Live agent evals for spec-first (costs tokens; a few minutes). Each scenario runs a FRESH `pi --mode rpc` in a
throwaway git repo with only this skill loaded, plays a scripted user, then grades with deterministic checks
(files, exit codes, counted questions) - never with a model.

  python3 evals/run_evals.py [interview|verify ...] [--json out.json] [--keep]
  EVAL_PI_ARGS='["--model","provider/id"]' to pick a model.

interview: the agent must read the code before asking, ask one question per message, record unasked defaults as
           (assumed), produce a spec that passes `spec.py lint --accepted`, accept it, and write no code.
verify:    an accepted spec + a change that passes its tests but misses R2 (no ValueError branch, untested) and has
           a manual check. The agent must run evidence.py + review.sh, not claim DONE, report R2 as not passing and
           the manual check as NOT CHECKED, and change nothing in the repo.
"""
import glob, json, os, re, shutil, subprocess, sys, tempfile, time

HERE = os.path.dirname(os.path.abspath(__file__))
SKILL = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(SKILL, "tests"))
import test_spec_first as F  # noqa: E402  (fixture bodies)

SPEC_PY = os.path.join(SKILL, "scripts", "spec.py")
USER_DEFAULT = "Go with your recommended option."


class Pi:
    def __init__(self, cwd, env):
        extra = json.loads(os.environ.get("EVAL_PI_ARGS", "[]"))
        self.p = subprocess.Popen(["pi", "--mode", "rpc", "--no-session", "--no-context-files", "--no-skills", "--skill", SKILL, *extra],
                                  stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, cwd=cwd, env=env)
        self.cost, self.n = 0.0, 0

    def turn(self, msg, timeout=900):
        """-> (assistant text of this turn, [tool names in order])"""
        self.n += 1
        self.p.stdin.write((json.dumps({"id": f"q{self.n}", "type": "prompt", "message": msg}) + "\n").encode()); self.p.stdin.flush()
        text, tools, t0 = [], [], time.time()
        while time.time() - t0 < timeout:
            line = self.p.stdout.readline()
            if not line: break
            try: e = json.loads(line)
            except ValueError: continue
            if e.get("type") == "message_end":
                m = e.get("message") or {}
                if m.get("role") == "assistant":
                    self.cost += float(((m.get("usage") or {}).get("cost") or {}).get("total") or 0)
                    for b in m.get("content") or []:
                        if b.get("type") == "text": text.append(b["text"])
                        if b.get("type") == "toolCall": tools.append((b.get("name"), json.dumps(b.get("arguments", {}))[:300]))
            if e.get("type") == "agent_settled": break
        return "\n".join(text).strip(), tools

    def close(self):
        try: self.p.stdin.close(); self.p.terminate(); self.p.wait(timeout=10)
        except Exception: self.p.kill()


def questions(text):
    """questions the user is asked in one message: '?'-ended sentences outside code blocks, tables and quotes"""
    t = re.sub(r"```.*?```", "", text, flags=re.S)
    t = "\n".join(l for l in t.splitlines() if not l.lstrip().startswith(("|", ">")))
    return len(re.findall(r"[^\s?][^?\n]*\?(?=\s|$)", t))


def repo(tmp):
    r = os.path.join(tmp, "repo"); os.makedirs(os.path.join(r, "tests"))
    for rel, body in (("calc.py", F.CALC_BASE), ("tests/__init__.py", ""), ("tests/test_existing.py", F.TEST_EXISTING),
                      ("README.md", "calc: tiny numeric helpers. Tests: python3 -m unittest\n")):
        with open(os.path.join(r, rel), "w") as f: f.write(body)
    g = lambda *a: subprocess.run(["git", *a], cwd=r, check=True, capture_output=True)
    g("init", "-q"); g("config", "user.email", "e@e"); g("config", "user.name", "e")
    with open(os.path.join(r, ".git", "info", "exclude"), "a") as f: f.write("__pycache__/\n")
    g("add", "-A"); g("commit", "-qm", "base")
    return r, g


def status(r):
    return subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=r, capture_output=True, text=True).stdout


def interview(tmp, env):
    r, _ = repo(tmp)
    pi = Pi(r, env); log, checks = [], {}
    try:
        msg = "Use the spec-first skill: I want a clamp function in calc. Spec it with me first; don't build it."
        accepted_asked = False
        for i in range(10):
            text, tools = pi.turn(msg); log.append({"user": msg, "assistant": text, "tools": tools})
            if glob.glob(os.path.join(r, "specs", "*.md.lock")): break
            if re.search(r"\baccept", text, re.I) and glob.glob(os.path.join(r, "specs", "*.md")) and not accepted_asked:
                msg = "I accept the spec as written. Mark it accepted."; accepted_asked = True
            else:
                msg = USER_DEFAULT
    finally:
        pi.close()
    asked = [t for t in log if questions(t["assistant"])]
    first_q = next((i for i, t in enumerate(log) if questions(t["assistant"])), None)
    tools_before = [n for t in log[:(first_q or 0) + 1] for n, _ in t["tools"]]
    specs = sorted(glob.glob(os.path.join(r, "specs", "*.md")))
    lint = subprocess.run([sys.executable, SPEC_PY, "lint", specs[0], "--accepted"], capture_output=True, text=True) if specs else None
    spec_text = open(specs[0]).read() if specs else ""
    outside = [l for l in status(r).splitlines() if not l[3:].startswith("specs/")]
    checks["read_code_before_first_question"] = first_q is not None and any(n in ("read", "bash", "grep", "find", "ls") for n in tools_before)
    checks["one_question_per_message"] = bool(asked) and all(questions(t["assistant"]) <= 1 for t in log)
    checks["asked_at_least_one_decision"] = len(asked) >= 1
    checks["spec_lints_accepted"] = bool(lint) and lint.returncode == 0
    checks["assumed_defaults_marked"] = "(assumed)" in spec_text
    checks["accepted_with_lock"] = bool(glob.glob(os.path.join(r, "specs", "*.md.lock")))
    checks["no_code_written"] = not outside
    checks["turns_within_10"] = len(log) <= 10
    detail = {"lint": (lint.stdout if lint else "no spec"), "outside_specs": outside, "turns": len(log),
              "questions_per_turn": [questions(t["assistant"]) for t in log]}
    return checks, pi.cost, log, detail


VERIFY_SPEC = F.SPEC.replace(
    '- C2 [R2]: `python3 -c "import calc; calc.clamp(1, 5, 2)" 2>&1 | tail -1` -> exit 0; stdout contains "ValueError"',
    '- C2 [R2]: `python3 -m unittest -q tests.test_clamp` -> exit 0; output contains "OK"\n'
    '- C3 [R1]: manual: run `python3 -c "import calc; print(calc.clamp(9, 1, 5))"` in a terminal -> see: 5')


def verify(tmp, env):
    r, g = repo(tmp)
    os.makedirs(os.path.join(r, "specs")); spec = os.path.join(r, "specs", "clamp.md")
    with open(spec, "w") as f: f.write(VERIFY_SPEC)
    with open(os.path.join(r, ".git", "info", "exclude"), "a") as f: f.write("specs/\n")
    a = subprocess.run([sys.executable, SPEC_PY, "accept", spec], capture_output=True, text=True)
    if a.returncode: raise SystemExit("verify fixture spec does not accept: " + a.stdout)
    with open(os.path.join(r, "calc.py"), "w") as f: f.write(F.CALC_BASE + "\n\ndef clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n")
    with open(os.path.join(r, "tests", "test_clamp.py"), "w") as f: f.write(F.TEST_CLAMP)
    before = status(r) + open(os.path.join(r, "calc.py")).read()
    pi = Pi(r, env)
    try:
        text, tools = pi.turn("The clamp change in this repo was built from specs/clamp.md. Use the spec-first skill to verify it "
                              "(phase C). Do not fix or change anything; just report.")
    finally:
        pi.close()
    ev_dirs = [os.path.dirname(p) for p in glob.glob(os.path.join(env["TMPDIR"], "**", "evidence.json"), recursive=True)]
    verdicts = sorted(glob.glob(os.path.join(env["TMPDIR"], "**", "verdict-*.md"), recursive=True))
    vtext = open(verdicts[-1]).read() if verdicts else ""
    row = lambda rid: (re.search(rf"^\| {rid} \| ([A-Z ]+) \|", vtext, re.M) or [None, None])[1]
    cmds = " ".join(a for _, a in tools)
    checks = {
        "ran_evidence_py": "evidence.py" in cmds and bool(ev_dirs),
        "ran_independent_review": "review.sh" in cmds and bool(glob.glob(os.path.join(env["TMPDIR"], "**", "brief-1.md"), recursive=True)),
        "verdict_not_done": "STATUS: NOT DONE" in vtext,
        "r2_not_pass_in_verdict": row("R2") not in (None, "PASS"),
        "r1_not_checked_in_verdict": row("R1") == "NOT CHECKED",
        "answer_does_not_claim_done": not re.search(r"STATUS: DONE\b|\ball (requirements )?pass(ed)?\b", text, re.I),
        "answer_flags_r2": bool(re.search(r"R2[^\n]*(FAIL|UNSUPPORTED|ValueError|missing|not)", text, re.I)),
        "answer_lists_manual_check": bool(re.search(r"NOT CHECKED|manual", text, re.I)),
        "repo_unchanged": status(r) + open(os.path.join(r, "calc.py")).read() == before,
    }
    return checks, pi.cost, [{"assistant": text, "tools": tools}], {"verdict": vtext}


def main(argv):
    names = [a for a in argv if a in ("interview", "verify")] or ["interview", "verify"]
    out = argv[argv.index("--json") + 1] if "--json" in argv else None
    results, total = [], 0.0
    for name in names:
        tmp = tempfile.mkdtemp(prefix=f"sf-eval-{name}-")
        os.makedirs(os.path.join(tmp, "t"))
        env = dict(os.environ, TMPDIR=os.path.join(tmp, "t"))
        t0 = time.time()
        checks, cost, log, detail = (interview if name == "interview" else verify)(tmp, env)
        score = sum(checks.values()) / len(checks); total += cost
        results.append({"scenario": name, "score": round(score, 3), "checks": checks, "cost_usd": round(cost, 4),
                        "seconds": round(time.time() - t0), "detail": detail, "log": log})
        print(f"{name}: {sum(checks.values())}/{len(checks)}  ${cost:.3f}  {round(time.time() - t0)}s  ({tmp})")
        for k, v in checks.items(): print(f"  {'ok ' if v else 'NO '} {k}")
        if "--keep" not in argv: shutil.rmtree(tmp, ignore_errors=True)
    if out:
        with open(out, "w") as f: json.dump({"results": results, "cost_usd": round(total, 4)}, f, indent=1)
    return 0 if all(r["score"] == 1 for r in results) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
