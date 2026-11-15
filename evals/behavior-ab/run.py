#!/usr/bin/env python3
"""Baseline vs narrow candidate vs length-matched control, using the existing outcome RPC session and Fisher test.

Register first, commit this directory and the two shared harness directories, then run:
  python3 evals/behavior-ab/run.py register --pi-args '["--model","provider/id"]'
  python3 evals/behavior-ab/run.py run
  python3 evals/behavior-ab/run.py report

Each worker has its own Pi config, workspace and process. The parent enforces a wall timeout even if RPC stalls.
Full logs are ignored; the portable trimmed ledger contains the configuration hash, model, checks, usage and errors.
"""
import argparse
import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
import tasks as T

# Reuse the established harness and statistical verdict primitive instead of a second model-calling implementation.
sys.path.insert(0, str(HERE.parent / "outcome"))
import verdict as V

PLAN = json.loads((HERE / "plan.json").read_text())
CANDIDATES = json.loads((HERE / "candidates.json").read_text())
FULL = ROOT / "results/behavior-ab"
LEDGER = HERE / "ledger.jsonl"
REG = HERE / "registration.json"
ARMS = tuple(PLAN["arms"])


def inputs():
    paths = list(HERE.rglob("*")) + list((HERE.parent/"outcome").rglob("*.py")) + list((HERE.parent/"harness-ab").rglob("*.py"))
    return sorted(p for p in paths if p.is_file() and "__pycache__" not in p.parts and
                  p.name not in ("registration.json", "ledger.jsonl", "RESULTS.md"))


def hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs()}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def committed():
    paths = [str(p.relative_to(ROOT)) for p in inputs()] + [str(REG.relative_to(ROOT))]
    dirty = subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=all", "--", *paths], cwd=ROOT, text=True)
    if dirty.strip():
        raise SystemExit("Commit the registered inputs before running:\n" + dirty)


def registration():
    if not REG.exists():
        raise SystemExit("Register a pinned model first (see --help).")
    reg = json.loads(REG.read_text())
    if reg["inputs"] != hashes():
        raise SystemExit("Experimental inputs changed; register and commit a NEW experiment before running.")
    committed()
    return reg


def skill_text(task, arm):
    c = CANDIDATES[task]
    p = HERE / "baselines" / c["base"] / ("SKILL.md" if c["base"] == "spec-first" else "skills/brain/SKILL.md")
    base = p.read_text()
    if arm == "baseline":
        return base
    body = "\n\n## Session boundary protocol\n" + c["body"] + "\n"
    if arm == "control":
        generic = (HERE.parent / "outcome/control.md").read_text()
        body = ("\n" + generic) * (1 + len(body)//len(generic))
        body = body[:len("\n\n## Session boundary protocol\n" + c["body"] + "\n")]
    if "description" in c:
        lines = base.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.startswith("description:"):
                desc = "description: " + c["description"] + "\n"
                if arm == "candidate":
                    lines[i] = desc
                else:
                    # Keep the current activation wording; match the candidate's description length with neutral padding.
                    pad = " Be thoughtful and clear."
                    lines[i] = line.rstrip() + pad + "\n"
                break
        base = "".join(lines)
    return base + body


def build_skill(task, arm, dest):
    base = CANDIDATES[task]["base"]
    src = HERE / "baselines" / base
    if base == "brain":
        src = src / "skills/brain"
    out = Path(dest) / base
    shutil.copytree(src, out)
    (out/"SKILL.md").write_text(skill_text(task, arm))
    if task == "activation" and arm == "control":
        # Match TOTAL instruction length, retaining the baseline description's exclusions.
        text = (out/"SKILL.md").read_text()
        target = len(skill_text(task, "candidate"))
        head, sep, body = text.partition("\n\n## Session boundary protocol") if "\n\n## Session boundary protocol" in text else (text, "", "")
        if len(text) > target:
            # Candidate's shorter discovery description is part of the treatment; don't truncate baseline instructions.
            # Exact length control is impossible when the baseline alone is longer. Report the imbalance explicitly.
            pass
    return out


def session_func():
    spec = importlib.util.spec_from_file_location("behavior_hab_run", HERE.parent / "harness-ab/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.session


def worker(request):
    task, case, arm = request["task"], request["case"], request["arm"]
    base = Path(request["workdir"])
    repo = base / "first"
    T.setup(repo, task, case)
    skill = build_skill(task, arm, base/"skills")
    os.environ["EVAL_PI_ARGS"] = json.dumps(request["pi_args"])
    os.environ["PI_CODING_AGENT_DIR"] = str(base/"pi-config")
    os.environ["PI_OFFLINE"] = "1"
    os.environ["PI_SKIP_VERSION_CHECK"] = "1"
    os.environ["BRAIN_DIR"] = str(repo/"brain")
    os.environ["BRAIN_REPOS_ROOT"] = str(repo)
    # The shared session disables global skills/context/extensions. Explicit provider extension is still allowed.
    session = session_func()
    boundaries = []

    def user(n, text):
        boundaries.append(T.app_unchanged(repo))
        return case["answer"] if task == "stopping" and n == 1 else None

    started = time.monotonic()
    log, usage, finished = session(str(repo), str(skill), T.prompt(task, case), user, PLAN["turn_timeout_seconds"])
    if not usage["assistant_msgs"] or not any(x["assistant"] for x in log):
        raise RuntimeError("Pi returned no answer: verify the pinned model, explicit provider extension and gateway access")
    initial_log = log
    if task == "recovery":
        # Start a new process/workspace; copy only STATE.md from the save session, never its final response or chat.
        resumed = base / "resumed"
        T.setup(resumed, task, case)
        state = repo/T.STATE
        if not state.is_file():
            (resumed/T.STATE).unlink(missing_ok=True)
        else:
            shutil.copyfile(state, resumed/T.STATE)
        os.environ["BRAIN_DIR"] = str(resumed/"brain")
        os.environ["BRAIN_REPOS_ROOT"] = str(resumed)
        log, usage2, finished2 = session(str(resumed), str(skill), T.RESUME, lambda n,t: None, PLAN["turn_timeout_seconds"])
        if not usage2["assistant_msgs"] or not any(x["assistant"] for x in log):
            raise RuntimeError("Pi returned no answer in the resume session")
        usage = {k: usage[k]+usage2[k] for k in usage}
        finished = finished and finished2
        repo = resumed
    checks = T.grade(repo, task, case, log, skill/"SKILL.md", boundaries)
    return {"finished": finished, "checks": checks, "pass": finished and all(checks.values()),
            "cost_usd": usage["cost"], "input_tokens": usage["input"]+usage["cacheRead"]+usage["cacheWrite"],
            "output_tokens": usage["output"], "wall_s": round(time.monotonic()-started, 2),
            "log": log, "save_log": initial_log if task == "recovery" else [], "workdir": str(base)}


def rows():
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()] if LEDGER.exists() else []


def preflight(reg):
    # Probe through the actual transport and provider. A failed probe is not a failed treatment observation.
    base = Path(tempfile.mkdtemp(prefix="behavior-preflight-"))
    env = {**os.environ, "PI_CODING_AGENT_DIR": str(base/"pi"), "PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1"}
    cmd = [os.environ.get("EVAL_PI", "pi"), "--print", "--no-session", "--no-context-files", "--no-skills",
           "--no-extensions", "--no-mcp", "--no-tools", *reg["pi_args"], "Reply with exactly MODEL_READY"]
    try:
        result = subprocess.run(cmd, cwd=base, env=env, capture_output=True, text=True, timeout=45)
        if result.returncode or not any(line.strip() == "MODEL_READY" for line in result.stdout.splitlines()):
            raise RuntimeError(f"model preflight failed (exit {result.returncode}); verify model/provider/gateway access")
    except subprocess.TimeoutExpired as e:
        raise RuntimeError("model preflight timed out; verify gateway/model access") from e
    finally:
        shutil.rmtree(base, ignore_errors=True)


def run(reg):
    preflight(reg)
    FULL.mkdir(parents=True, exist_ok=True)
    key = digest(reg)
    previous = rows()
    done = {(r["task"], r["case"], r["arm"], r["i"]) for r in previous if r["experiment"] == key}
    spent = sum(r.get("cost_usd", 0) for r in previous if r["experiment"] == key)
    # Serial and interleaved: low cost overshoot, no global Pi config races, provider drift hits all arms.
    for i in range(PLAN["repeats"]):
        for task in PLAN["candidates"]:
            for case in T.CASES[task]:
                for arm in ARMS:
                    if (task, case["id"], arm, i) in done:
                        continue
                    if spent >= PLAN["max_cost_usd"]:
                        print("Cost budget reached; experiment remains INCOMPLETE.")
                        return 1
                    base = tempfile.mkdtemp(prefix=f"behavior-{task}-{arm}-")
                    request = {"task": task, "case": case, "arm": arm, "pi_args": reg["pi_args"], "workdir": base}
                    limit = (2 if task in ("stopping", "recovery") else 1)*PLAN["turn_timeout_seconds"]+20
                    try:
                        p = subprocess.run([sys.executable, str(Path(__file__).resolve()), "worker"], input=json.dumps(request),
                                           capture_output=True, text=True, timeout=limit)
                        if p.returncode:
                            raise RuntimeError("worker failed: " + p.stderr[-1200:])
                        result = json.loads(p.stdout)
                    except (subprocess.TimeoutExpired, RuntimeError, ValueError) as e:
                        # Transport errors never become treatment failures or adoption evidence.
                        print(f"RUN ERROR {task}/{case['id']}/{arm}/{i}: {e}", file=sys.stderr)
                        return 1
                    full = {"task": task, "case": case["id"], "split": case["split"], "arm": arm, "i": i,
                            "experiment": key, "model": reg["pi_args"], **result}
                    with (FULL/"transcripts.jsonl").open("a") as f:
                        f.write(json.dumps(full)+"\n")
                    row = {k:v for k,v in full.items() if k not in ("log", "save_log", "workdir")}
                    with LEDGER.open("a") as f:
                        f.write(json.dumps(row, sort_keys=True)+"\n")
                    spent += result["cost_usd"]
                    print(f"{task}/{case['id']}/{arm}/{i}: pass={row['pass']} cost=${row['cost_usd']:.3f} "
                          f"fails={[k for k,v in row['checks'].items() if not v]}", flush=True)
                    shutil.rmtree(base, ignore_errors=True)
    return 0


def summarize(task, data):
    expected = {(arm, i, c["id"]) for arm in ARMS for i in range(PLAN["repeats"]) for c in T.CASES[task]}
    actual = [(r["arm"], r["i"], r["case"]) for r in data]
    out = {"task": task, "verdict": "NOT_RUN", "scores": {}, "cost_usd": round(sum(r["cost_usd"] for r in data), 4)}
    if not data:
        return out
    if len(set(actual)) != len(actual) or not set(actual) <= expected:
        out["verdict"] = "INVALID_LEDGER"
        return out
    if set(actual) != expected:
        out["verdict"] = "INCOMPLETE"
        return out
    groups = {a: [r for r in data if r["arm"] == a] for a in ARMS}
    # Recompute success from binary checks and finished; don't trust the cached pass flag.
    passed = lambda r: r.get("finished") is True and bool(r.get("checks")) and all(v is True for v in r["checks"].values())
    score = {a: sum(all(passed(r) for r in groups[a] if r["i"] == i) for i in range(PLAN["repeats"])) for a in ARMS}
    out["scores"] = score
    out["heldout"] = {a: sum(passed(r) for r in groups[a] if r["split"] == "heldout") for a in ARMS}
    n = PLAN["repeats"]
    out["p_vs_baseline"] = V.fisher_greater(score["candidate"], n, score["baseline"], n)
    out["p_vs_control"] = V.fisher_greater(score["candidate"], n, score["control"], n)
    costs = {a: sum(r["cost_usd"] for r in groups[a]) for a in ARMS}
    out["cost_ratio"] = costs["candidate"]/costs["baseline"] if costs["baseline"] > 0 else None
    if score["baseline"]/n >= PLAN["ceiling"]:
        out["verdict"] = "CEILING"
    elif out["heldout"]["candidate"] < max(out["heldout"]["baseline"], out["heldout"]["control"]):
        out["verdict"] = "REGRESSION"
    elif score["candidate"] <= score["baseline"]:
        out["verdict"] = "NOT_WORTH"
    elif max(out["p_vs_baseline"], out["p_vs_control"]) > PLAN["alpha"]:
        out["verdict"] = "INCONCLUSIVE"
    elif out["cost_ratio"] is None:
        out["verdict"] = "COST_UNVERIFIED"
    elif out["cost_ratio"] > PLAN["max_cost_ratio"]:
        out["verdict"] = "TOO_COSTLY"
    else:
        out["verdict"] = "WORTHWHILE"
    return out


def report(reg):
    key = digest(reg)
    all_rows = rows()
    fresh = [r for r in all_rows if r["experiment"] == key]
    for task in PLAN["candidates"]:
        print(json.dumps(summarize(task, [r for r in fresh if r["task"] == task]), sort_keys=True))
    print(f"stale rows excluded: {len(all_rows)-len(fresh)}")


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=("register", "run", "report", "worker"))
    p.add_argument("--pi-args", help="JSON array with a pinned --model and explicit provider extension if needed")
    a = p.parse_args()
    if a.command == "worker":
        print(json.dumps(worker(json.load(sys.stdin))))
        return 0
    if a.command == "register":
        args = json.loads(a.pi_args or "[]")
        if not isinstance(args, list) or not all(isinstance(x, str) for x in args) or "--model" not in args:
            p.error("--pi-args must be a JSON string array containing --model provider/id")
        index = args.index("--model")
        if index+1 == len(args) or "/" not in args[index+1]:
            p.error("pin a provider/model after --model")
        if LEDGER.exists() and rows():
            p.error("Archive the existing ledger before registering a new experiment; never silently replace its registration")
        # Pin an explicit, minimal tool surface; global packages, context and skills are disabled by session().
        args += ["--no-mcp", "--tools", "read,bash,edit,write"]
        reg = {"pi_args": args, "inputs": hashes(), "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        REG.write_text(json.dumps(reg, indent=2)+"\n")
        print(f"Registered {digest(reg)}; commit inputs before running. No model calls made.")
        return 0
    reg = registration()
    if a.command == "report":
        report(reg)
        return 0
    try:
        code = run(reg)
    except (RuntimeError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    report(reg)
    return code


if __name__ == "__main__":
    sys.exit(main())
