#!/usr/bin/env python3
"""Live A/B for a skill (costs tokens, ~20 s per answer). Each probe in skills/<dir>/evals/probes.json is answered
in a FRESH `pi --mode rpc --no-session --no-context-files --no-skills` session, in an empty temp dir. The `with` arm
also passes `--skill skills/<dir>`, and nothing else is loaded. Grading is deterministic: every `expect` group
(any-of, case-insensitive substrings) must appear in the answer. No model grades.

  python3 scripts/skill_ab.py <dir> [--samples 3] [--arms with,without] [--gate] [--with-path DIR] [--out FILE]
  EVAL_PI_ARGS='["--model","provider/id"]' to pick a model. EVAL_PI=<binary> replaces `pi` (tests use a fake).
  --with-path DIR   load this skill in the `with` arm instead of skills/<dir> (an outside skill, same probes)
  --out FILE        write the results here instead of results/<dir>-<utc>.json (evals/gate.py reads it)

Prints per-probe correct counts per arm and how often the skill was actually read. Writes full answers to
results/<dir>-<utc>.json (gitignored). Use --gate to exit 1 unless `with` beats `without` on every probe.
Samples run sequentially. Measured cost is about $0.10 per answer with no context files; a probe × 2 arms × 3 samples costs about $0.6.
"""
import datetime, hashlib, json, os, subprocess, sys, tempfile, time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def ask(q, skill_dir=None, timeout=300):
    """-> (answer text, [(tool, args-json)], cost_usd, finished)"""
    extra = json.loads(os.environ.get("EVAL_PI_ARGS", "[]"))
    cmd = [os.environ.get("EVAL_PI", "pi"), "--mode", "rpc", "--no-session", "--no-context-files", "--no-skills", *(["--skill", skill_dir] if skill_dir else []), *extra]
    cwd = tempfile.mkdtemp(prefix="skill-ab-")
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, cwd=cwd)
    p.stdin.write(json.dumps({"id": "q", "type": "prompt", "message": q}) + "\n"); p.stdin.flush()
    text, tools, cost, done, t0 = [], [], 0.0, False, time.time()
    try:
        for line in p.stdout:
            try: e = json.loads(line)
            except ValueError: continue
            if e.get("type") == "message_end":
                m = e.get("message") or {}
                if m.get("role") == "assistant":
                    cost += float(((m.get("usage") or {}).get("cost") or {}).get("total") or 0)
                    for b in m.get("content") or []:
                        if b.get("type") == "text": text.append(b["text"])
                        if b.get("type") == "toolCall": tools.append((b.get("name"), json.dumps(b.get("arguments", {}))[:300]))
            if e.get("type") in ("agent_settled", "agent_end"): done = True; break
            if time.time() - t0 > timeout: break
    finally:
        try: p.stdin.close(); p.terminate(); p.wait(timeout=10)
        except Exception: p.kill()
    return "\n".join(text).strip(), tools, cost, done


def missing(probe, text):
    low = text.lower()
    return [g[0] for g in probe["expect"] if not any(a.lower() in low for a in g)]


def main(argv):
    if not argv or argv[0].startswith("-"): print(__doc__); return 2
    d = argv[0]; skill = os.path.join(ROOT, "skills", d)
    samples = int(argv[argv.index("--samples") + 1]) if "--samples" in argv else 3
    arms = (argv[argv.index("--arms") + 1] if "--arms" in argv else "with,without").split(",")
    probes_path = os.path.join(skill, "evals", "probes.json")
    probes = json.load(open(probes_path))
    with_skill = os.path.abspath(argv[argv.index("--with-path") + 1]) if "--with-path" in argv else skill
    rows, total = [], 0.0
    for pr in probes:
        for arm in arms:
            for n in range(samples):
                text, tools, cost, done = ask(pr["q"], with_skill if arm == "with" else None)
                miss = missing(pr, text); total += cost
                read = any(t == "read" and "SKILL.md" in a for t, a in tools)
                rows.append({"probe": pr["id"], "split": pr.get("split", "dev"), "arm": arm, "n": n, "correct": not miss and done, "missing": miss,
                             "skill_read": read, "tools": tools, "cost_usd": round(cost, 4), "finished": done, "answer": text})
                print(f"{'OK   ' if not miss and done else 'WRONG'} {pr['id']} {arm} #{n + 1} read={read} ${cost:.3f} missing={miss}", flush=True)
    print("\nprobe | " + " | ".join(arms))
    gate_ok = True
    for pr in probes:
        score = {a: sum(r["correct"] for r in rows if r["probe"] == pr["id"] and r["arm"] == a) for a in arms}
        reads = sum(r["skill_read"] for r in rows if r["probe"] == pr["id"] and r["arm"] == "with")
        print(f"{pr['id']} | " + " | ".join(f"{score[a]}/{samples}" + (f" (read {reads}/{samples})" if a == "with" else "") for a in arms))
        if "with" in score and "without" in score and score["with"] <= score["without"]: gate_ok = False
    out = argv[argv.index("--out") + 1] if "--out" in argv else \
        os.path.join(ROOT, "results", f"{d}-{datetime.datetime.now(datetime.timezone.utc):%Y%m%dT%H%M%SZ}.json")
    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    with open(probes_path, "rb") as f:
        probes_sha = hashlib.sha256(f.read()).hexdigest()
    with open(out, "w") as f:
        json.dump({"skill": d, "samples": samples, "arms": arms, "with_path": os.path.relpath(with_skill, ROOT),
                   "pi_args": json.loads(os.environ.get("EVAL_PI_ARGS", "[]")), "probes_sha256": probes_sha,
                   "date": datetime.date.today().isoformat(), "cost_usd": round(total, 3), "rows": rows}, f, indent=1)
    print(f"cost ${total:.2f}; answers: {out}")
    return 1 if ("--gate" in argv and not gate_ok) else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
