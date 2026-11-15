#!/usr/bin/env python3
"""Run outcome tasks with a real model (LAPTOP ONLY: costs tokens; never runs in CI). Appends one trimmed row per run
to evals/outcome/ledger/<task>.jsonl (commit it) and the full transcript to results/outcome/<task>.jsonl (gitignored).

  EVAL_PI_ARGS='["--model","provider/id"]' python3 evals/outcome/run.py [--tasks dst,doubt] [--n N] [--jobs 3] [--arms none,skill,control]
  python3 evals/outcome/run.py --build-arms     build the arm dirs only and print their sizes
  then: python3 evals/outcome/verdict.py && python3 evals/status.py --write && git add evals/ && git commit

Arms (each a fresh `pi --mode rpc --no-session --no-context-files --no-skills --no-extensions` in a throwaway repo):
  none     no skill
  skill    skills/<dir> (without its evals/ and tests/)
  control  the skill's own name and description (so it gets loaded as often) over generic careful-engineering text
           (control.md) of the same length: rules out "any extra prompt helps"
Refuses to run unless the model is pinned and the skill, task and control files are committed (the pre-registration).
Resumable: a (task, arm, i) already in the ledger with the current hashes is skipped. Run it detached:
  nohup python3 evals/outcome/run.py --tasks property-tests > results/outcome/run.log 2>&1 &
"""
import concurrent.futures as cf
import datetime
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "evals", "harness-ab"))
import registry as T  # noqa: E402
import verdict as V  # noqa: E402


def _hab_session():
    """evals/harness-ab/run.py's session(): one multi-turn `pi --mode rpc` session (imported by path: same module name)"""
    import importlib.util
    spec = importlib.util.spec_from_file_location("hab_run", os.path.join(ROOT, "evals", "harness-ab", "run.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m.session


session = _hab_session()

ARMS_DIR = os.path.join(ROOT, "results", "outcome", "arms")
FULL = os.path.join(ROOT, "results", "outcome")
LOCK, SERIAL = threading.Lock(), threading.Lock()


def build_arm(skill, arm):
    if arm == "none":
        return None
    d = os.path.join(ARMS_DIR, f"{skill}-{arm}")
    if os.path.exists(d):
        shutil.rmtree(d)
    if arm == "skill":
        os.makedirs(d)
        for rel, p in V.skill_files(skill):
            os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
            shutil.copy(p, os.path.join(d, rel))
        return d
    text = open(os.path.join(ROOT, "skills", skill, "SKILL.md")).read()
    end = text.index("\n---", 4) + 4
    head, body_len = text[:end], len(text) - end
    generic = open(V.CONTROL).read()
    body = ""
    while len(body) < body_len:
        body += "\n" + generic
    os.makedirs(d)
    with open(os.path.join(d, "SKILL.md"), "w") as f:
        f.write(head + "\n" + body[:body_len])
    return d


def preflight(task_ids):
    args = json.loads(os.environ.get("EVAL_PI_ARGS", "[]"))
    if "--model" not in args:
        raise SystemExit("pin the model: EVAL_PI_ARGS='[\"--model\",\"provider/id\"]'")
    paths = {V.CONTROL}
    for t in task_ids:
        paths |= set(T.TASKS[t]["files"]) | {os.path.join(ROOT, "skills", T.TASKS[t]["skill"])}
    rel = [os.path.relpath(p, ROOT) for p in sorted(paths)]
    dirty = subprocess.run(["git", "status", "--porcelain", "--", *rel], cwd=ROOT, capture_output=True, text=True).stdout
    if dirty.strip():
        raise SystemExit("commit the skill, task and control files first (pre-registration):\n" + dirty)
    return json.dumps(args), subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()


def one(task_id, arm, i, arm_dir, model, head, hashes):
    t = T.TASKS[task_id]
    base = tempfile.mkdtemp(prefix=f"out-{task_id}-{arm}-{i}-")
    r = os.path.join(base, "repo")
    os.makedirs(r)
    t["setup"](r)
    lock = SERIAL if t.get("serial") else threading.Lock()
    with lock:
        t0 = time.time()
        log, usage, finished = session(r, arm_dir, t["prompt"], t["user"], t["turn_timeout"])
    final = log[-1]["assistant"] if log else ""
    all_text = "\n".join(x["assistant"] for x in log)
    bash = [str((c["args"] or {}).get("command", "")) for x in log for c in x["tools"] if c["name"] == "bash"]
    reads = [str((c["args"] or {}).get("path", "")) for x in log for c in x["tools"] if c["name"] == "read"]
    checks, detail = t["grade"](r, final, all_text, bash)
    row = {"task": task_id, "arm": arm, "i": i, "model": model, "git_head": head, "hashes": hashes,
           "date": datetime.date.today().isoformat(), "finished": finished, "checks": checks,
           "pass": V.passed({"finished": finished, "checks": checks}, t["prereg"]["primary"]),
           "skill_read": any("SKILL.md" in x for x in reads), "cost_usd": round(usage["cost"], 4),
           "input_tokens": usage["input"] + usage["cacheRead"] + usage["cacheWrite"], "wall_s": round(time.time() - t0)}
    full = {**row, "detail": detail, "workdir": base, "log": log}
    with LOCK:
        with open(os.path.join(V.LEDGER, f"{task_id}.jsonl"), "a") as f:
            f.write(json.dumps(row, sort_keys=True) + "\n")
        with open(os.path.join(FULL, f"{task_id}.jsonl"), "a") as f:
            f.write(json.dumps(full, default=str) + "\n")
        print(f"{task_id}-{arm} #{i}: pass={row['pass']} finished={finished} read={row['skill_read']} ${usage['cost']:.2f} "
              f"{row['wall_s']}s fails={[k for k, v in checks.items() if not v]}", flush=True)


def main(a):
    task_ids = (a[a.index("--tasks") + 1] if "--tasks" in a else ",".join(T.TASKS)).split(",")
    unknown = set(task_ids) - set(T.TASKS)
    if unknown:
        raise SystemExit(f"unknown tasks {sorted(unknown)}; have {sorted(T.TASKS)}")
    arms = (a[a.index("--arms") + 1] if "--arms" in a else ",".join(V.ARMS)).split(",")
    dirs = {(t, arm): build_arm(T.TASKS[t]["skill"], arm) for t in task_ids for arm in arms}
    for (t, arm), d in dirs.items():
        print(f"arm {t}-{arm}: " + (f"SKILL.md {len(open(os.path.join(d, 'SKILL.md')).read())} chars" if d else "no skill"))
    if "--build-arms" in a:
        return 0
    model, head = preflight(task_ids)
    os.makedirs(FULL, exist_ok=True)
    os.makedirs(V.LEDGER, exist_ok=True)
    jobs = int(a[a.index("--jobs") + 1]) if "--jobs" in a else 3
    work = []
    for t in task_ids:
        hashes = V.current_hashes(t)
        n = int(a[a.index("--n") + 1]) if "--n" in a else T.TASKS[t]["prereg"]["n"]
        done = {(r["arm"], r["i"]) for r in V.load_rows(t) if r.get("hashes") == hashes}
        # interleave arms so drift in the model/provider over the run hits every arm alike
        work += [(t, arm, i, hashes) for i in range(n) for arm in arms if (arm, i) not in done]
    print(f"{len(work)} runs to do", flush=True)
    with cf.ThreadPoolExecutor(jobs) as ex:
        fs = [ex.submit(one, t, arm, i, dirs[(t, arm)], model, head, h) for t, arm, i, h in work]
        for f in cf.as_completed(fs):
            if f.exception():
                print("RUN ERROR", repr(f.exception()), flush=True)
    V.main([])
    print("next: python3 evals/status.py --write && git add evals/outcome/ledger evals/STATUS.md && git commit")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
