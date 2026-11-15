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
import math
import os
import signal
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
_task_spec = importlib.util.spec_from_file_location("behavior_tasks", HERE / "tasks.py")
T = importlib.util.module_from_spec(_task_spec)
_task_spec.loader.exec_module(T)

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
    paths = list(HERE.rglob("*")) + list((HERE.parent/"outcome").rglob("*.py")) + list((HERE.parent/"harness-ab").rglob("*.py")) + [ROOT/"scripts/split_skill.py", HERE.parent/"outcome/control.md"]
    return sorted(p for p in paths if p.is_file() and "__pycache__" not in p.parts and
                  p.name not in ("registration.json", "ledger.jsonl", "RESULTS.md"))


def hashes():
    return {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs()}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def extension_hashes(args):
    """Pin explicit provider code as well as its local path; never copy auth/config into the experiment."""
    out = {}
    for i, arg in enumerate(args):
        if arg not in ("-e", "--extension"):
            continue
        if i+1 == len(args):
            raise ValueError("missing explicit extension path")
        raw = args[i+1]
        p = Path(raw).expanduser()
        if not p.exists():
            raise ValueError("explicit extension path does not exist: " + raw)
        files = [p] if p.is_file() else [f for f in p.rglob("*") if f.is_file() and
                f.suffix in (".ts", ".js", ".json") and not {"node_modules", ".git"} & set(f.relative_to(p).parts)]
        out[raw] = {str(f.relative_to(p) if p.is_dir() else f.name):hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(files)}
    return out


def pi_version():
    with tempfile.TemporaryDirectory(prefix="behavior-pi-version-") as tmp:
        env = {**os.environ, "PI_CODING_AGENT_DIR": tmp, "PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1"}
        p = subprocess.run([os.environ.get("EVAL_PI", "pi"), "--version"], env=env, capture_output=True, text=True, timeout=10)
        if p.returncode or not p.stdout.strip():
            raise RuntimeError("Cannot identify the installed Pi version")
        return p.stdout.strip()


def committed():
    # Compare actual bytes with HEAD; status alone misses ignored files and skip-worktree/assume-unchanged edits.
    for p in inputs()+[REG]:
        rel = str(p.relative_to(ROOT))
        blob = subprocess.run(["git", "show", "HEAD:"+rel], cwd=ROOT, capture_output=True)
        if p.is_symlink() or blob.returncode or blob.stdout != p.read_bytes():
            raise SystemExit("Commit the registered inputs before running: " + rel)


def registration(verify_provider=True):
    if not REG.exists():
        raise SystemExit("Register a pinned model first (see --help).")
    reg = json.loads(REG.read_text())
    if reg["inputs"] != hashes():
        raise SystemExit("Experimental inputs changed; register and commit a NEW experiment before running.")
    if verify_provider and reg["extensions"] != extension_hashes(reg["pi_args"]):
        raise SystemExit("Provider extension changed; register a new experiment before running.")
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
    if "description" in c and arm == "candidate":
        lines = base.splitlines(keepends=True)
        for i, line in enumerate(lines):
            if line.startswith("description:"):
                desc = "description: " + c["description"]
                # Equal discovery-description length; preserve the baseline description verbatim in the control.
                pad = " Follow the user's request carefully."
                desc += (pad * (1 + len(line)//len(pad)))[:max(0, len(line.rstrip())-len(desc))]
                lines[i] = desc + "\n"
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
    return out


def session_func():
    spec = importlib.util.spec_from_file_location("behavior_hab_run", HERE.parent / "harness-ab/run.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.session


def file_state(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in Path(root).rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts
            and "__pycache__" not in p.parts}


def worker(request):
    task, case, arm = request["task"], request["case"], request["arm"]
    base = Path(request["workdir"])
    repo = base / "first"
    T.setup(repo, task, case)
    skill = build_skill(task, arm, base/"skills")
    os.environ["EVAL_PI_ARGS"] = json.dumps([os.path.expanduser(x) for x in request["pi_args"]])
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
    extra_checks = {}
    if task == "recovery":
        extra_checks["save_only_state"] = T.only_state_changed(repo)
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
        before = file_state(resumed)
        log, usage2, finished2 = session(str(resumed), str(skill), T.RESUME, lambda n,t: None, PLAN["turn_timeout_seconds"])
        extra_checks["resume_readonly"] = before == file_state(resumed)
        if not usage2["assistant_msgs"] or not any(x["assistant"] for x in log):
            raise RuntimeError("Pi returned no answer in the resume session")
        usage = {k: usage[k]+usage2[k] for k in usage}
        finished = finished and finished2
        repo = resumed
    checks = T.grade(repo, task, case, log, skill/"SKILL.md", boundaries)
    checks.update(extra_checks)
    return {"finished": finished, "checks": checks, "pass": finished and all(checks.values()),
            "cost_usd": usage["cost"], "input_tokens": usage["input"]+usage["cacheRead"]+usage["cacheWrite"],
            "output_tokens": usage["output"], "wall_s": round(time.monotonic()-started, 2),
            "log": log, "save_log": initial_log if task == "recovery" else [], "workdir": str(base)}


def rows():
    return [json.loads(line) for line in LEDGER.read_text().splitlines() if line.strip()] if LEDGER.exists() else []


def bounded_process(cmd, timeout, **kwargs):
    """Kill the whole worker group on timeout/interruption, including a stuck Pi child."""
    data = kwargs.pop("input", None)
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True, start_new_session=True, **kwargs)
    try:
        stdout, stderr = p.communicate(data, timeout=timeout)
        return subprocess.CompletedProcess(cmd, p.returncode, stdout, stderr)
    except BaseException:
        try:
            os.killpg(p.pid, signal.SIGTERM)
            p.wait(timeout=2)
        except (ProcessLookupError, subprocess.TimeoutExpired):
            pass
        finally:
            # A worker may exit while a child ignores SIGTERM. Kill the remaining group either way.
            try:
                os.killpg(p.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            p.wait()
        raise
    finally:
        for stream in (p.stdin, p.stdout, p.stderr):
            if stream is not None:
                stream.close()


def preflight(reg):
    # Probe through the actual transport and provider. A failed probe is not a failed treatment observation.
    if "pi_version" in reg and pi_version() != reg["pi_version"]:
        raise RuntimeError("Pi version changed; register a new experiment before running")
    base = Path(tempfile.mkdtemp(prefix="behavior-preflight-"))
    env = {**os.environ, "PI_CODING_AGENT_DIR": str(base/"pi"), "PI_OFFLINE": "1", "PI_SKIP_VERSION_CHECK": "1"}
    cmd = [os.environ.get("EVAL_PI", "pi"), "--print", "--no-session", "--no-context-files", "--no-skills",
           "--no-extensions", *[os.path.expanduser(x) for x in reg["pi_args"]],
           "--no-mcp", "--no-tools", "Reply with exactly MODEL_READY"]
    try:
        result = bounded_process(cmd, cwd=base, env=env, timeout=45)
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
                        p = bounded_process([sys.executable, str(Path(__file__).resolve()), "worker"], input=json.dumps(request), timeout=limit)
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
                    if result["cost_usd"] <= 0:
                        print("Provider prices unavailable; cost cap cannot be enforced. Stopping without an adoption verdict.")
                        return 1
    return 0


def summarize(task, data):
    expected = {(arm, i, c["id"]) for arm in ARMS for i in range(PLAN["repeats"]) for c in T.CASES[task]}
    out = {"task": task, "verdict": "NOT_RUN", "scores": {}, "cost_usd": 0}
    if not data:
        return out
    if any(not isinstance(r, dict) or {"arm", "i", "case", "cost_usd", "checks"}-r.keys() or
           not isinstance(r["checks"], dict) or type(r["i"]) is not int or
           not isinstance(r["arm"], str) or not isinstance(r["case"], str) for r in data):
        out["verdict"] = "INVALID_LEDGER"
        return out
    actual = [(r["arm"], r["i"], r["case"]) for r in data]
    cases = {c["id"]:c for c in T.CASES[task]}
    malformed = any(r.get("split") != cases.get(r.get("case"), {}).get("split") or
                    set(r.get("checks", {})) != T.primary(task, cases.get(r.get("case"), {})) or
                    not isinstance(r.get("finished"), bool) or
                    any(type(v) is not bool for v in r.get("checks", {}).values()) or
                    not isinstance(r.get("cost_usd"), (float, int)) or
                    not math.isfinite(r.get("cost_usd", float("nan"))) or r.get("cost_usd", -1) < 0 for r in data)
    if malformed or len(set(actual)) != len(actual) or not set(actual) <= expected:
        out["verdict"] = "INVALID_LEDGER"
        return out
    out["cost_usd"] = round(sum(r["cost_usd"] for r in data), 4)
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
    elif all(s == 0 for s in score.values()):
        out["verdict"] = "FLOOR"
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
    if any(r.get("task") not in PLAN["candidates"] or r.get("model") != reg["pi_args"] for r in fresh):
        print("INVALID_LEDGER: unknown task or model differs from registration", file=sys.stderr)
        return 1
    invalid = False
    for task in PLAN["candidates"]:
        summary = summarize(task, [r for r in fresh if r["task"] == task])
        invalid |= summary["verdict"] == "INVALID_LEDGER"
        print(json.dumps(summary, sort_keys=True))
    print(f"stale rows excluded: {len(all_rows)-len(fresh)}")
    return int(invalid)


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
        reg = {"pi_args": args, "pi_version": pi_version(), "extensions": extension_hashes(args),
               "inputs": hashes(), "registered_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
        REG.write_text(json.dumps(reg, indent=2)+"\n")
        print(f"Registered {digest(reg)}; commit inputs before running. No model calls made.")
        return 0
    reg = registration(verify_provider=a.command == "run")
    if a.command == "report":
        return report(reg)
    if report(reg):
        return 1
    try:
        code = run(reg)
    except (RuntimeError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return max(code, report(reg))


if __name__ == "__main__":
    sys.exit(main())
