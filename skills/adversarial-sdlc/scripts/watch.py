#!/usr/bin/env python3
"""watch.py [PROJECT] [--once] [--every SECONDS]: a live view of an adversarial-sdlc run, from its records only.

Shows whether the orchestrator is running (it holds an flock on .sdlc/.mutex.lock), the stages of the loop (build
and gate, attack rounds and fix rounds, calibration, report) as done / running / pending, what is running now (role
or gate step, with its latest log line), and an estimated completion range. The range is honest about the loop: a
round that finds nothing ends it early (the low end of the remaining work), a round that finds something adds a
fix round (the high end). Read-only; no cluster or network access.
"""
from __future__ import annotations

import argparse
import fcntl
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

# Rough minutes per stage (measured on a Kotlin parser project, k8s backend, 2026-11-05/06): they only weight the estimate.
W = {"build": 4, "gate": 5, "attack": 28, "calibrate": 45, "report": 1}


def conf(project: Path, key: str, default: str) -> str:
    v = os.environ.get(key)
    if v:
        return v
    f = project / "sdlc.config"
    if f.exists():
        m = re.findall(rf"^{key}=([^\s#]+)", f.read_text(), re.M)
        if m:
            return m[-1].strip('"')
    return default


def orchestrator(run: Path) -> tuple[bool, str]:
    """(running, elapsed) from the flock the orchestrator holds while it lives."""
    lock = run / ".mutex.lock"
    if not lock.exists():
        return False, ""
    with open(lock, "a") as f:
        try:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            fcntl.flock(f, fcntl.LOCK_UN)
            return False, ""
        except OSError:
            pass
    pids = subprocess.run(["lsof", "-t", str(lock)], capture_output=True, text=True).stdout.split()
    et = subprocess.run(["ps", "-o", "etime=", "-p", pids[0]], capture_output=True, text=True).stdout.strip() if pids else ""
    return True, et


def gates(run: Path, label: str) -> list[Path]:
    return sorted(p for p in (run / "gate").glob(f"*-{label}-*") if p.is_dir()) if (run / "gate").is_dir() else []


def gate_state(g: Path) -> tuple[str, str]:
    rep = g / "gate-report.json"
    if rep.exists():
        try:
            return ("done", "pass" if json.loads(rep.read_text()).get("pass") else "fail")
        except ValueError:
            return ("done", "?")
    steps = sorted((g / "steps").glob("*.json")) if (g / "steps").is_dir() else []
    done = []
    for s in steps:
        try:
            d = json.loads(s.read_text())
            done.append(f"{d['name']} {d['status']}")
        except (ValueError, KeyError):
            pass
    return ("running", ", ".join(done) or "starting")


def last_line(p: Path) -> str:
    try:
        with open(p, "rb") as f:
            f.seek(max(0, p.stat().st_size - 4096))
            lines = [ln for ln in f.read().decode(errors="replace").splitlines() if ln.strip()]
        return lines[-1][:110] if lines else ""
    except OSError:
        return ""


def stages(project: Path, run: Path) -> tuple[list[tuple[str, str, str]], float, float, float]:
    """[(stage, state, detail)], minutes done, minutes left if the next round is dry, minutes left at most."""
    rounds = int(conf(project, "SDLC_MAX_ROUNDS", "3"))
    calib = conf(project, "SDLC_CALIBRATE", "auto") == "auto"
    out: list[tuple[str, str, str]] = []
    done = 0.0
    lo = hi = 0.0

    def build_round(r: int) -> str:
        nonlocal done
        gs = gates(run, f"build-r{r}")
        if not gs:
            out.append((f"build + gate (round {r})", "pending", ""))
            return "pending"
        st, det = gate_state(gs[-1])
        it = re.search(r"-i(\d+)-", gs[-1].name)
        tag = f"iteration {it.group(1)}" if it else ""
        if st == "done" and det == "pass":
            out.append((f"build + gate (round {r})", "done", f"gate passed, {tag}"))
            done += W["build"] + W["gate"]
            return "pass"
        out.append((f"build + gate (round {r})", "running", f"{tag}: {det}"))
        done += W["build"]
        return "running"

    ended = False
    state = build_round(0)
    for r in range(1, rounds + 1):
        if ended:
            break
        rd = run / "attack" / f"round-{r}"
        rec = rd / "attacker.done"
        if state != "pass":
            out.append((f"attack round {r}", "pending", ""))
            lo += W["attack"] if r == 1 else 0
            hi += W["attack"] + W["build"] + W["gate"]
            continue
        if rec.exists():
            try:
                summ = json.loads(rec.read_text())["summary"]
                summ = json.loads(summ) if isinstance(summ, str) else summ
            except (ValueError, KeyError):
                summ = {}
            n = int(summ.get("reproduced", 0))
            out.append((f"attack round {r}", "done", f"{n} reproduced, {summ.get('discarded', 0)} discarded"))
            done += W["attack"]
            if n == 0:
                ended = True
                break
            state = build_round(r)
            if state != "pass":
                hi += W["gate"]
                lo += W["gate"]
        elif (rd / "attacker.failed").exists():
            out.append((f"attack round {r}", "failed", last_line(rd / "attacker.failed")))
            ended = True
            break
        else:
            running = rd.is_dir()
            out.append((f"attack round {r}", "running" if running else "pending", ""))
            lo += W["attack"]
            hi += W["attack"] + W["build"] + W["gate"]
            state = "pending"
    # Rounds after an unfinished one: up to the maximum at most, none at least.
    if not ended and len([s for s in out if s[0].startswith("attack")]) < rounds:
        left = rounds - len([s for s in out if s[0].startswith("attack")])
        hi += left * (W["attack"] + W["build"] + W["gate"])
    cal = sorted(p for p in (run / "calibration").glob("*") if p.is_dir()) if (run / "calibration").is_dir() else []
    if calib:
        if ended and cal and (cal[-1] / "calibration.json").exists():
            out.append(("calibration", "done", f"{cal[-1].name}"))
            done += W["calibrate"]
        elif ended and cal:
            phase = "scoring" if (cal[-1] / "attack").is_dir() else "planting / checking bugs"
            out.append(("calibration", "running", phase))
            lo += W["calibrate"] / 2
            hi += W["calibrate"] / 2
            done += W["calibrate"] / 2
        else:
            out.append(("calibration", "pending", "after a round finds nothing"))
            lo += W["calibrate"]
            hi += W["calibrate"]
    rep = run / "REPORT.md"
    fresh = rep.exists() and (not cal or rep.stat().st_mtime >= cal[-1].stat().st_mtime)
    if fresh and ended:
        out.append(("report", "done", "REPORT.md"))
        done += W["report"]
    else:
        out.append(("report", "pending", ""))
        lo += W["report"]
        hi += W["report"]
    return out, done, lo, hi


def current(run: Path) -> list[str]:
    lines = []
    logs = sorted((p for p in (run / "logs").glob("*-*.log")), key=lambda p: p.stat().st_mtime) if (run / "logs").is_dir() else []
    roles = [p for p in logs if re.match(r"(spec|tests|build|attack|plant|referee|judge|design)-\d", p.name)]
    if roles:
        p = roles[-1]
        age = time.time() - p.stat().st_mtime
        if age < 300:
            lines.append(f"role {p.name.split('-')[0]:8} active (log updated {age:.0f}s ago): {last_line(p)}")
    for g in reversed(sorted((run / "gate").glob("*")) if (run / "gate").is_dir() else []):
        st, det = gate_state(g)
        if st == "running" and time.time() - g.stat().st_mtime < 3600:
            started = json.loads((g / "started.json").read_text()).get("t", time.time()) if (g / "started.json").exists() else time.time()
            lines.append(f"gate {g.name}: {time.time() - started:.0f}s, steps done: {det}")
        break
    return lines or ["(nothing active right now: between stages, or waiting)"]


def render(project: Path) -> str:
    run = project / ".sdlc"
    alive, et = orchestrator(run)
    st, done, lo, hi = stages(project, run)
    pct_hi = 100 * done / (done + lo) if done + lo else 100
    pct_lo = 100 * done / (done + hi) if done + hi else 100
    width = 40
    a, b = round(width * pct_lo / 100), round(width * pct_hi / 100)
    bar = "█" * a + "▒" * (b - a) + "·" * (width - b)
    mark = {"done": "✓", "running": "▶", "pending": "·", "failed": "✗"}
    out = [f"adversarial-sdlc: {project.name}    {time.strftime('%H:%M:%S')}",
           f"orchestrator: {'RUNNING (' + et + ')' if alive else 'not running'}",
           "",
           f"[{bar}] {pct_lo:.0f}–{pct_hi:.0f}%  (estimate: low if every round finds bugs, high if the next finds none)",
           ""]
    out += [f"  {mark.get(s, '?')} {name:26} {det}" for name, s, det in st]
    out += ["", "now:"] + [f"  {ln}" for ln in current(run)]
    if not alive and (run / "REPORT.md").exists():
        out += ["", f"finished: {run / 'REPORT.md'}"]
    return "\n".join(out)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("project", nargs="?", default=".")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--every", type=float, default=5.0)
    a = ap.parse_args(argv)
    project = Path(a.project).resolve()
    if not (project / ".sdlc").is_dir():
        print(f"watch: no .sdlc/ in {project}", file=sys.stderr)
        return 2
    if a.once:
        print(render(project))
        return 0
    try:
        while True:
            sys.stdout.write("\033[H\033[2J" + render(project) + "\n")
            sys.stdout.flush()
            time.sleep(a.every)
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
