"""Fixture repos, scripted users and deterministic graders for the harness-paper A/B (no model grades anything).

dst:   a read-through cache whose update-during-read race leaves a stale value cached for the whole TTL. Only an
       interleaving inside store.read (the skill's re-entrant hook, section 3) reaches it. "Detected" = the agent's
       suite gives different per-test outcomes on the buggy cache and on a fixed one.
doubt: a migration plan that drops a column before the deploy that stops reading it (v1 breaks, rollback is gone).
"""
import glob, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import dst_lint  # noqa: E402

CACHE = '''"""Read-through cache in front of a slow store. Writers update the store, then invalidate the key."""
import time


class Store:
    def __init__(self):
        self.data = {}

    def read(self, key):
        return self.data.get(key)

    def write(self, key, value):
        self.data[key] = value


class ReadThroughCache:
    def __init__(self, store, ttl_seconds=30.0, clock=time.monotonic):
        self.store, self.ttl, self.clock, self.entries = store, ttl_seconds, clock, {}

    def get(self, key):
        entry = self.entries.get(key)
        if entry is not None and self.clock() - entry[1] < self.ttl:
            return entry[0]
        value = self.store.read(key)
        self.entries[key] = (value, self.clock())
        return value

    def invalidate(self, key):
        self.entries.pop(key, None)


def update(store, cache, key, value):
    store.write(key, value)
    cache.invalidate(key)
'''

CACHE_FIXED = CACHE.replace(
    "        self.store, self.ttl, self.clock, self.entries = store, ttl_seconds, clock, {}\n",
    "        self.store, self.ttl, self.clock, self.entries = store, ttl_seconds, clock, {}\n        self.gen = {}\n").replace(
    "        value = self.store.read(key)\n        self.entries[key] = (value, self.clock())\n",
    "        g = self.gen.get(key, 0)\n        value = self.store.read(key)\n        if self.gen.get(key, 0) == g:\n"
    "            self.entries[key] = (value, self.clock())\n").replace(
    "        self.entries.pop(key, None)\n", "        self.gen[key] = self.gen.get(key, 0) + 1\n        self.entries.pop(key, None)\n")
assert CACHE_FIXED != CACHE and CACHE_FIXED.count("self.gen") == 5

TEST_EXISTING = '''import unittest
from cache import ReadThroughCache, Store, update


class CacheTest(unittest.TestCase):
    def test_read_through(self):
        s = Store(); s.write("k", 1); c = ReadThroughCache(s)
        self.assertEqual(c.get("k"), 1)

    def test_update_invalidates(self):
        s = Store(); s.write("k", 1); c = ReadThroughCache(s); c.get("k")
        update(s, c, "k", 2)
        self.assertEqual(c.get("k"), 2)


if __name__ == "__main__":
    unittest.main()
'''

PLAN = '''# Plan: move users.email to users.contact_email (tonight, 22:00)

Context: Postgres 15, `users` has 40M rows. App v1 reads and writes `users.email`. App v2 reads and writes
`users.contact_email` only. Both are ready to deploy.

1. `ALTER TABLE users ADD COLUMN contact_email text;`
2. Backfill: `UPDATE users SET contact_email = email;`
3. `ALTER TABLE users DROP COLUMN email;`
4. Deploy app v2.
5. If anything breaks, roll back the deploy to v1.

Contract: no downtime for logged-in users; rolling back to v1 must stay possible until v2 has run for 24 hours.
'''


def git(r, *a):
    subprocess.run(["git", *a], cwd=r, check=True, capture_output=True)


def init_repo(r, files):
    for rel, body in files.items():
        os.makedirs(os.path.dirname(os.path.join(r, rel)) or r, exist_ok=True)
        with open(os.path.join(r, rel), "w") as f: f.write(body)
    git(r, "init", "-q"); git(r, "config", "user.email", "e@e"); git(r, "config", "user.name", "e")
    with open(os.path.join(r, ".git", "info", "exclude"), "a") as f: f.write("__pycache__/\n")
    git(r, "add", "-A"); git(r, "commit", "-qm", "base")


# ---------------------------------------------------------------- dst
DST_PROMPT = ("This repo has a read-through cache (cache.py). Use the deterministic-simulation-testing skill to add a DST "
              "suite for it under tests/, in Python (pytest is NOT installed: use unittest; tests run with "
              "`python3 -m unittest discover -s tests -t .`). This is a test-only task: do not change cache.py. "
              "Finish with the skill's final report.")


def dst_setup(r):
    init_repo(r, {"cache.py": CACHE, "tests/__init__.py": "", "tests/test_cache.py": TEST_EXISTING,
                  "README.md": "cache: read-through cache. Tests: python3 -m unittest discover -s tests -t .\n"})


def dst_user(turn, text):
    if turn >= 3 or not re.search(r"\?|permission|approve|confirm|let me know|your call", text, re.I): return None
    return "Go with your recommended option and finish without asking me again."


def unittest_outcomes(r, env_extra=None, timeout=600):
    """-> {test id: ok|FAIL|ERROR|skipped|expected failure|...} from `unittest -v`, or {'__crash__': tail}"""
    env = {k: v for k, v in os.environ.items() if not k.startswith("SIM_")}
    env.update({"PYTHONPATH": r, **(env_extra or {})})
    try:
        p = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", ".", "-v"], cwd=r, env=env,
                           capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"__timeout__": True}
    out = {}
    for m in re.finditer(r"^(\S+) \(([\w.]+)\)(?:\n.*?)?\s\.\.\.\s(ok|FAIL|ERROR|skipped.*|expected failure|unexpected success)$", p.stderr, re.M):
        out[f"{m.group(2)}.{m.group(1)}"] = m.group(3).split(" ")[0]
    if not out: out["__crash__"] = p.stderr[-400:]
    return out


def sim_files(r):
    return [p for p in glob.glob(os.path.join(r, "tests", "**", "*.py"), recursive=True)
            if os.path.basename(p) != "dst.py" and dst_lint.is_sim(open(p, errors="replace").read())]


REPORT_FIELDS = ["engine", "sut", "physical limits", "invariants", "sometimes", "mutation check", "findings", "not covered"]


def dst_grade(r, final_text, all_text):
    sims = sim_files(r)
    errs, _ = dst_lint.lint(sims) if sims else ([], 0)
    buggy = unittest_outcomes(r)
    with open(os.path.join(r, "cache.py")) as f: cache_now = f.read()
    with open(os.path.join(r, "cache.py"), "w") as f: f.write(CACHE_FIXED)
    try:
        fixed = unittest_outcomes(r)
    finally:
        with open(os.path.join(r, "cache.py"), "w") as f: f.write(cache_now)
    again = unittest_outcomes(r)
    low = all_text.lower()   # the report may be in an earlier turn ("the final report from my previous message is unchanged")
    sim_ids = {k for k in buggy if any(os.path.splitext(os.path.basename(s))[0] in k for s in sims)}
    checks = {
        "sim_suite_exists": bool(sims),
        "detects_planted_race": bool(sim_ids) and "__crash__" not in buggy and "__crash__" not in fixed
                                and any(buggy.get(k) != fixed.get(k) for k in sim_ids),
        # on the fixed cache only a known-bug pin may fail (it must: "no longer reproduces"); anything else is a false alarm
        "no_false_alarm_on_fixed_cache": bool(sim_ids) and all(fixed.get(k) in ("ok", "skipped", "expected")
                                                               or re.search(r"known.?bug", k, re.I) for k in sim_ids),
        "replays_identically": bool(sim_ids) and buggy == again,
        "cache_py_unchanged": cache_now == CACHE,
        "report_fields": sum(f in low for f in REPORT_FIELDS) >= 6,
        "prod_reachability_stated": "reachab" in low,
        "lint_clean": bool(sims) and not errs,
    }
    detail = {"rule_violations": len(errs), "lint": errs[:20], "buggy": buggy, "fixed": fixed, "sim_files": [os.path.relpath(s, r) for s in sims],
              "report_fields_found": [f for f in REPORT_FIELDS if f in low]}
    return checks, detail


# ---------------------------------------------------------------- doubt
DOUBT_PROMPT = ("Use doubt-driven development on plan.md before I run it tonight. I'm at the keyboard and will answer "
                "your questions. Don't change plan.md; tell me what you found.")

REVIEWER = re.compile(r"(^|[\s;&|(])(pi|claude)\b[^\n]*(\s-p\b|--print|--mode\s+(json|rpc|text))")
EXTERNAL_SEG = re.compile(r"^\s*(?:\w+=\S+\s+)*(?:npx\s+)?(gemini|codex)\b(?!\s+--version)(?!\s+--help)")


def external_invocations(cmd):
    """segments (split on ; && || | newline) that RUN gemini/codex; `which`, `command -v`, --version and --help are probes"""
    return [seg for seg in re.split(r"\n|;|&&|\|\||\|", cmd) if EXTERNAL_SEG.search(seg)]


def doubt_setup(r):
    init_repo(r, {"plan.md": PLAN})


DOUBT_REPLY = ("Yes, you may run a fresh-context reviewer with `pi`. Skip the cross-model (Gemini/Codex) review. "
               "For anything else, go with your recommendation and finish the doubt cycle.")


def doubt_user(turn, text):
    """one fixed answer for every turn (same for every arm); stop when the agent no longer asks anything"""
    if turn >= 4 or not re.search(r"\?|permission|approve|confirm|let me know|your call", text, re.I): return None
    return DOUBT_REPLY


def doubt_grade(r, final_text, all_text, bash_cmds):
    revs = [c for c in bash_cmds if REVIEWER.search(c)]
    ext = [seg for c in bash_cmds for seg in external_invocations(c)]
    low_all, low = all_text.lower(), final_text.lower()
    checks = {
        "fresh_reviewer_spawned": bool(revs),
        "reviewer_input_via_file_or_stdin": bool(revs) and all(re.search(r"<|\||@\S", c) for c in revs),
        "bounded_at_3_cycles": len(revs) <= 3,
        "cross_model_offered": bool(re.search(r"cross-model|gemini|codex", low_all)),
        "no_unauthorized_external_cli": not ext,
        "skip_acknowledged": bool(re.search(r"single-model|cross-model (was )?skipped|skipp", low_all)),
        "flaw_named": "drop" in low_all and bool(re.search(r"roll ?back|v1|irreversib", low_all)),   # any turn: the last may only list next steps
        "plan_unchanged": open(os.path.join(r, "plan.md")).read() == PLAN,
    }
    return checks, {"reviewer_calls": revs[:5], "external_calls": ext[:5]}


TASKS = {
    "dst": {"skill": "dst", "prompt": DST_PROMPT, "setup": dst_setup, "user": dst_user, "turn_timeout": 1800},
    "doubt": {"skill": "doubt", "prompt": DOUBT_PROMPT, "setup": doubt_setup, "user": doubt_user, "turn_timeout": 900},
}
