#!/usr/bin/env python3
"""Live A/B for two ideas from arXiv 2609.00006 (costs tokens; run detached). Pre-registered plan and thresholds:
docs/ab-harness-paper-plan.md. Each run is a FRESH `pi --mode rpc --no-session --no-context-files --no-skills
--no-extensions --skill <arm dir>` in a throwaway git repo; a scripted user answers; grading is deterministic (tasks.py).

  python3 evals/harness-ab/run.py [--tasks dst,doubt] [--n 5] [--jobs 3] [--out results/harness-ab/run.jsonl]
  python3 evals/harness-ab/run.py --build-arms        only build the arm skill dirs and print their sizes
  EVAL_PI_ARGS='["--model","provider/id"]' to pick a model.

Arms: A = skill as on this branch's base, B = progressive disclosure (scripts/split_skill.py), C (dst only) = A plus
scripts/dst_lint.py and a hard rule to run it. Resumable: a (task, arm, i) already in --out is skipped.
Report: python3 evals/harness-ab/report.py <out.jsonl>
"""
import concurrent.futures as cf, json, os, shutil, subprocess, sys, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE); sys.path.insert(0, os.path.join(ROOT, "scripts"))
import tasks as T, split_skill  # noqa: E402

ARMS_DIR = os.path.join(ROOT, "results", "harness-ab", "arms")
KEEP = {"dst": ["^## Hard rules", "^## Checklist"], "doubt": ["^## Overview", "^## When to Use", "^## The Process"]}
LINT_RULE = ("7. **Run `python3 scripts/dst_lint.py <your sim test files>` before you report, and fix every ERROR.** "
             "It checks rules 3 and 6, naming, replay and anti-vacuity mechanically.\n")
ARMS = {"dst": ["A", "B", "C"], "doubt": ["A", "B"]}
LOCK = threading.Lock()
SERIAL = {"doubt": threading.Lock()}   # the skill writes /tmp/doubt-prompt.md: two doubt runs at once would clobber each other


def build_arms():
    out = {}
    for task, arms in ARMS.items():
        src = os.path.join(ROOT, "skills", T.TASKS[task]["skill"])
        for arm in arms:
            d = os.path.join(ARMS_DIR, f"{task}-{arm}")
            if arm == "A":
                if os.path.exists(d): shutil.rmtree(d)
                shutil.copytree(src, d, ignore=shutil.ignore_patterns("__pycache__", "evals", "tests"))
            elif arm == "B":
                a = os.path.join(ARMS_DIR, f"{task}-A")
                split_skill.split(a, d, KEEP[task]); ok, msg = split_skill.check(a, d)
                assert ok, msg
            elif arm == "C":
                a = os.path.join(ARMS_DIR, f"{task}-A")
                if os.path.exists(d): shutil.rmtree(d)
                shutil.copytree(a, d); os.makedirs(os.path.join(d, "scripts"), exist_ok=True)
                shutil.copy(os.path.join(HERE, "dst_lint.py"), os.path.join(d, "scripts", "dst_lint.py"))
                p = os.path.join(d, "SKILL.md"); s = open(p).read()
                anchor = "\n\n## Checklist"
                assert anchor in s and "6. **The PR tier stays cheap:**" in s
                s = s.replace(anchor, "\n" + LINT_RULE + anchor, 1).replace(
                    "- [ ] 7  Determinism verified", "- [ ] 7  `scripts/dst_lint.py` clean; determinism verified", 1)
                open(p, "w").write(s)
            out[f"{task}-{arm}"] = (d, len(open(os.path.join(d, "SKILL.md")).read()))
    return out


def session(cwd, skill_dir, first, user, turn_timeout):
    extra = json.loads(os.environ.get("EVAL_PI_ARGS", "[]"))
    cmd = ["pi", "--mode", "rpc", "--no-session", "--no-context-files", "--no-skills", "--no-extensions", "--skill", skill_dir, *extra]
    env = {**os.environ, "TMPDIR": os.path.join(cwd, "..", "tmp")}
    os.makedirs(env["TMPDIR"], exist_ok=True)
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=cwd, env=env)
    log, usage, msg, n = [], {"input": 0, "output": 0, "cacheRead": 0, "cacheWrite": 0, "cost": 0.0, "assistant_msgs": 0}, first, 0
    finished = True
    try:
        while msg is not None:
            n += 1
            p.stdin.write(json.dumps({"id": f"q{n}", "type": "prompt", "message": msg}) + "\n"); p.stdin.flush()
            text, tools, t0, settled = [], [], time.time(), False
            while time.time() - t0 < turn_timeout:
                line = p.stdout.readline()
                if not line: break
                try: e = json.loads(line)
                except ValueError: continue
                if e.get("type") == "message_end":
                    m = e.get("message") or {}
                    if m.get("role") == "assistant":
                        u = m.get("usage") or {}; usage["assistant_msgs"] += 1
                        for k in ("input", "output", "cacheRead", "cacheWrite"): usage[k] += int(u.get(k) or 0)
                        usage["cost"] += float((u.get("cost") or {}).get("total") or 0)
                        for b in m.get("content") or []:
                            if b.get("type") == "text": text.append(b["text"])
                            if b.get("type") == "toolCall": tools.append({"name": b.get("name"), "args": b.get("arguments", {})})
                if e.get("type") == "agent_settled": settled = True; break   # not agent_end: it precedes agent_settled, and the next turn would read a stale settle
            t = "\n".join(text).strip(); log.append({"user": msg, "assistant": t, "tools": tools})
            if not settled: finished = False; break
            msg = user(n, t)
    finally:
        try: p.stdin.close(); p.terminate(); p.wait(timeout=10)
        except Exception: p.kill()
    return log, usage, finished


def one(task, arm, i, arm_dir, out):
    t = T.TASKS[task]; base = tempfile.mkdtemp(prefix=f"hab-{task}-{arm}-{i}-"); r = os.path.join(base, "repo"); os.makedirs(r)
    t["setup"](r)
    with SERIAL.get(task) or threading.Lock():
        t0 = time.time(); log, usage, finished = session(r, arm_dir, t["prompt"], t["user"], t["turn_timeout"])
    final = log[-1]["assistant"] if log else ""
    all_text = "\n".join(x["assistant"] for x in log)
    bash = [str((c["args"] or {}).get("command", "")) for x in log for c in x["tools"] if c["name"] == "bash"]
    reads = [str((c["args"] or {}).get("path", "")) for x in log for c in x["tools"] if c["name"] == "read"]
    if task == "dst": checks, detail = T.dst_grade(r, final, all_text)
    else: checks, detail = T.doubt_grade(r, final, all_text, bash)
    row = {"task": task, "arm": arm, "i": i, "finished": finished, "checks": checks, "passed": sum(checks.values()), "n_checks": len(checks),
           "usage": usage, "turns": len(log), "tool_calls": sum(len(x["tools"]) for x in log), "wall_s": round(time.time() - t0),
           "skill_reads": [x for x in reads if "SKILL.md" in x or "/reference/" in x], "detail": detail, "workdir": base,
           "final": final[-3000:], "log": [{"user": x["user"], "assistant": x["assistant"][-4000:],
           "tools": [{"name": c["name"], "args": json.dumps(c["args"])[:1500]} for c in x["tools"]]} for x in log], "model": os.environ.get("EVAL_PI_ARGS", "default")}
    with LOCK:
        with open(out, "a") as f: f.write(json.dumps(row) + "\n")
        print(f"{task}-{arm} #{i}: {row['passed']}/{row['n_checks']} finished={finished} ${usage['cost']:.2f} "
              f"in={usage['input'] + usage['cacheRead'] + usage['cacheWrite']} {row['wall_s']}s "
              f"fails={[k for k, v in checks.items() if not v]}", flush=True)


def main(a):
    arms = build_arms()
    for k, (d, n) in arms.items(): print(f"arm {k}: SKILL.md {n} chars  {d}")
    if "--build-arms" in a: return 0
    tasks = (a[a.index("--tasks") + 1] if "--tasks" in a else "dst,doubt").split(",")
    n = int(a[a.index("--n") + 1]) if "--n" in a else 5
    jobs = int(a[a.index("--jobs") + 1]) if "--jobs" in a else 3
    out = a[a.index("--out") + 1] if "--out" in a else os.path.join(ROOT, "results", "harness-ab", "run.jsonl")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    done = set()
    if os.path.exists(out):
        for line in open(out):
            x = json.loads(line); done.add((x["task"], x["arm"], x["i"]))
    # interleave arms so a drift in the model/provider over the run hits every arm alike
    work = [(t, arm, i) for i in range(n) for t in tasks for arm in ARMS[t] if (t, arm, i) not in done]
    print(f"{len(work)} runs to do ({len(done)} already in {out})", flush=True)
    with cf.ThreadPoolExecutor(jobs) as ex:
        fs = [ex.submit(one, t, arm, i, arms[f"{t}-{arm}"][0], out) for t, arm, i in work]
        for f in cf.as_completed(fs):
            if f.exception(): print("RUN ERROR", repr(f.exception()), flush=True)
    print("done; report: python3 evals/harness-ab/report.py " + out)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
