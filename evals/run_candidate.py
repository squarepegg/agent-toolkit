#!/usr/bin/env python3
"""Run one registered candidate: check it is committed, fetch an outside skill at its pinned sha if any, then run
scripts/skill_ab.py (both arms) into results/<id>.json. Costs model tokens. Then: python3 evals/gate.py <candidate> <results>.

  EVAL_PI_ARGS='["--model","provider/id"]' python3 evals/run_candidate.py evals/candidates/<id>.json [--out FILE]
"""
import json, os, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
import gate  # noqa: E402


def main(argv):
    if not argv or argv[0].startswith("-"):
        print(__doc__)
        return 2
    path = argv[0]
    out = argv[argv.index("--out") + 1] if "--out" in argv else None
    try:
        with open(path) as fh:
            c = json.load(fh)
        gate.validate_candidate(c, path)
        gate.registered(path)
        if "--model" not in json.loads(os.environ.get("EVAL_PI_ARGS", "[]")):
            raise gate.BadInput("pin the model: EVAL_PI_ARGS='[\"--model\",\"provider/id\"]'")
    except (gate.BadInput, ValueError, OSError) as e:
        print(f"run_candidate: {e}", file=sys.stderr)
        return 2
    out = out or os.path.join(ROOT, "results", f"{c['id']}.json")
    cmd = [sys.executable, os.path.join(ROOT, "scripts", "skill_ab.py"), c["skill"], "--samples", str(c.get("samples", 3)), "--out", out]
    with tempfile.TemporaryDirectory() as tmp:
        if c.get("fetch"):
            dest = os.path.join(tmp, "fetched")
            for step in (["git", "init", "-q", dest],
                         ["git", "-C", dest, "fetch", "-q", "--depth", "1", c["fetch"]["repo"], c["fetch"]["ref"]],
                         ["git", "-C", dest, "checkout", "-q", "FETCH_HEAD"]):
                subprocess.run(step, check=True)
            cmd += ["--with-path", os.path.join(dest, c["with_path"])]
        elif c.get("with_path"):
            cmd += ["--with-path", os.path.join(ROOT, c["with_path"])]
        return subprocess.run(cmd).returncode


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
