"""Outcome task (mutation score): does skills/correctness-first make an agent write a workload that finds MORE bugs?

store.py is a small log-structured key-value store with restart() and an all-or-nothing put_many(). The agent is asked
to add tests that tell us whether it is correct. Grading runs the agent's tests against the real store (they must
pass) and against six plausible wrong versions (mutants, one edit each), each in a different class the skill names:

  restart_reverse   an overwritten key comes back with its old value after restart            (easy: overwrite + restart)
  skip_boundary     the last entry of every full segment is lost on restart                   (long run, whole-state compare)
  merge_newest      merging drops tombstones of the wrong segments: deleted keys come back    (long run, cold keys, restart)
  merge_first_wins  a merge keeps the first value of a hot key, not the last                  (long run, hot keys, restart)
  falsy_lost        a stored 0 / "" is lost on restart                                        (value distribution)
  batch_partial     put_many applies the pairs before a bad one                               (fault / error injection)

The score of a run is the number of mutants its tests kill (0..6), or 0 if its tests fail on the real store, it edited
store.py, or it did not finish. Arms are compared on score, not pass/fail: see evals/outcome/verdict.py.

History: v1 (bank.py lost update) and v2 (one planted bug in this store) were found by reading the code (no-skill
4/4 twice): any single bug an agent can read saturates. See evals/negative-results.md.
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "harness-ab"))
from tasks import init_repo, unittest_outcomes  # noqa: E402

MUT_NAMES = ["restart_reverse", "skip_boundary", "merge_newest", "merge_first_wins", "falsy_lost", "batch_partial"]
# Pre-registered (changing any of this changes the task hash and stales old ledger rows)
PREREG = {"n": 10, "alpha": 0.05, "ceiling": 0.8,
          "score": {"kills": [f"kills_{m}" for m in MUT_NAMES], "gate": ["passes_real", "src_unchanged"]},
          "primary": ["passes_real", "src_unchanged", *[f"kills_{m}" for m in MUT_NAMES]]}

REAL = '''"""A small log-structured key-value store.

Writes append to the newest segment of the log. A segment holds 128 entries; when it is full a new one is started, and
when more than four full (sealed) segments exist, two of them are merged into one to bound the log. restart()
simulates a process restart: the in-memory index is thrown away and rebuilt by replaying every segment in order. After
restart() the store must behave exactly as it did before.

put_many(items) is all-or-nothing: if any value is not an int, float or str it raises TypeError and the store is
unchanged.
"""


class Store:
    SEGMENT_SIZE = 128
    MAX_SEALED = 4

    def __init__(self):
        self.segments = [[]]
        self.index = {}

    def put(self, key, value):
        self._append(("put", key, value))
        self.index[key] = value

    def put_many(self, items):
        items = list(items)
        for _, value in items:
            if not isinstance(value, (int, float, str)):
                raise TypeError(f"unsupported value {value!r}")
        for key, value in items:
            self.put(key, value)

    def delete(self, key):
        self._append(("del", key, None))
        self.index.pop(key, None)

    def get(self, key):
        return self.index.get(key)

    def restart(self):
        self.index = {}
        for segment in self.segments:
            for op, key, value in segment:
                if op == "put":
                    self.index[key] = value
                else:
                    self.index.pop(key, None)

    def _append(self, entry):
        self.segments[-1].append(entry)
        if len(self.segments[-1]) >= self.SEGMENT_SIZE:
            self.segments.append([])
            if len(self.segments) - 1 > self.MAX_SEALED:
                self._merge()

    def _merge(self):
        """Merge the two oldest sealed segments into one, keeping only the live puts."""
        i = 0
        live = {}
        for op, key, value in self.segments[i] + self.segments[i + 1]:
            if op == "put":
                live[key] = value
            else:
                live.pop(key, None)
        self.segments[i:i + 2] = [[("put", k, v) for k, v in live.items()]]
'''


def _sub(old, new):
    assert REAL.count(old) == 1, old
    return REAL.replace(old, new)


MUTANTS = {
    "restart_reverse": _sub("            for op, key, value in segment:\n                if op == \"put\":",
                            "            for op, key, value in reversed(segment):\n                if op == \"put\":"),
    "skip_boundary": _sub("            for op, key, value in segment:\n                if op == \"put\":",
                          "            for op, key, value in (segment if segment is self.segments[-1] else segment[:-1]):\n                if op == \"put\":"),
    "merge_newest": _sub("        i = 0\n", "        i = len(self.segments) - 4\n"),
    "merge_first_wins": _sub("                live[key] = value\n", "                live.setdefault(key, value)\n"),
    "falsy_lost": _sub("                if op == \"put\":\n                    self.index[key] = value",
                       "                if op == \"put\" and value:\n                    self.index[key] = value"),
    "batch_partial": _sub("""        items = list(items)
        for _, value in items:
            if not isinstance(value, (int, float, str)):
                raise TypeError(f"unsupported value {value!r}")
        for key, value in items:
            self.put(key, value)""", """        for key, value in items:
            if not isinstance(value, (int, float, str)):
                raise TypeError(f"unsupported value {value!r}")
            self.put(key, value)"""),
}
assert all(m != REAL for m in MUTANTS.values()) and set(MUTANTS) == set(MUT_NAMES)

TEST_EXISTING = '''import unittest
from store import Store


class StoreTest(unittest.TestCase):
    def test_put_get(self):
        s = Store()
        s.put("a", 1)
        self.assertEqual(s.get("a"), 1)

    def test_delete(self):
        s = Store()
        s.put("a", 1)
        s.delete("a")
        self.assertIsNone(s.get("a"))

    def test_restart_keeps_data(self):
        s = Store()
        s.put("a", 1)
        s.put("b", 2)
        s.restart()
        self.assertEqual((s.get("a"), s.get("b")), (1, 2))

    def test_put_many(self):
        s = Store()
        s.put_many([("a", 1), ("b", "x")])
        self.assertEqual((s.get("a"), s.get("b")), (1, "x"))


if __name__ == "__main__":
    unittest.main()
'''

SKILL_SENTENCE = "Use the correctness-first-design skill. "
PROMPT_NONE = ("store.py is about to ship. Add tests under tests/ that would tell us whether it is correct. This is a "
               "test-only task: do not change store.py. Stdlib only; tests run with "
               "`python3 -m unittest discover -s tests -t .`. Tell me what you found.")
PROMPT = SKILL_SENTENCE + PROMPT_NONE   # skill and control arms; the none arm gets PROMPT_NONE (a missing skill stalls the agent)


def setup(r):
    init_repo(r, {"store.py": REAL, "tests/__init__.py": "", "tests/test_store.py": TEST_EXISTING,
                  "README.md": "store. Tests: python3 -m unittest discover -s tests -t .\n"})


def user(turn, text):
    if turn >= 3 or not re.search(r"\?|permission|approve|confirm|let me know|your call", text, re.I):
        return None
    return "Go with your recommendation and finish without asking me again."


def _with(r, src):
    d = tempfile.mkdtemp(prefix="cf-grade-")
    try:
        shutil.copytree(os.path.join(r, "tests"), os.path.join(d, "tests"))
        with open(os.path.join(d, "store.py"), "w") as f:
            f.write(src)
        return unittest_outcomes(d, timeout=120)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _green(out):
    return bool(out) and all(v in ("ok", "skipped", "expected") for v in out.values())


def grade(r, final_text, all_text, bash_cmds):
    real = _with(r, REAL)
    checks, detail, low = {"passes_real": _green(real)}, {"real": {k: v for k, v in real.items() if v != "ok"}}, all_text.lower()
    for name, src in MUTANTS.items():
        o = _with(r, src)
        checks[f"kills_{name}"] = not _green(o) and "__crash__" not in o and "__timeout__" not in o
        detail[name] = {k: v for k, v in o.items() if v != "ok"}
    with open(os.path.join(r, "store.py")) as f:
        checks["src_unchanged"] = f.read() == REAL
    # secondary (not in the score): the skill's vocabulary
    checks["names_workload"] = bool(re.search(r"model.based|oracle|reference model|differential|random(ized)? (op|work|seq)|workload", low))
    checks["names_gray_failure"] = bool(re.search(r"gr[ae]y.?failure", low))
    return checks, detail


# --------------------------------------------------------------------------- grader fixtures (CI pins the grader both ways)
REFERENCE_TESTS = TEST_EXISTING.replace('''

if __name__''', '''

    def test_model_based_workload(self):
        import random
        for seed in range(20):
            rng = random.Random(seed)
            s, model = Store(), {}
            hot = [f"h{j}" for j in range(3)]
            for i in range(1500):
                op = rng.choice(["put", "put", "delete", "get", "restart"] if i > 5 else ["put"])
                k = rng.choice(hot) if rng.random() < 0.3 else f"k{rng.randrange(150)}"
                if op == "put":
                    v = rng.choice([0, "", i, str(i), i + 0.5])
                    s.put(k, v)
                    model[k] = v
                elif op == "delete":
                    s.delete(k)
                    model.pop(k, None)
                elif op == "restart" and rng.random() < 0.1:
                    s.restart()
                self.assertEqual(s.get(k), model.get(k), (seed, i, k))
            s.restart()
            for key in set(model) | {f"k{j}" for j in range(150)} | set(hot):
                self.assertEqual(s.get(key), model.get(key), (seed, key))

    def test_overwrite_survives_restart(self):
        s = Store()
        s.put("a", 1)
        s.put("a", 2)
        s.restart()
        self.assertEqual(s.get("a"), 2)

    def test_put_many_is_all_or_nothing(self):
        s = Store()
        s.put("keep", 1)
        for bad in (None, [1], {"x": 1}):
            with self.assertRaises(TypeError):
                s.put_many([("a", 1), ("b", bad), ("c", 3)])
            self.assertEqual((s.get("a"), s.get("b"), s.get("c"), s.get("keep")), (None, None, None, 1))
            s.restart()
            self.assertEqual((s.get("a"), s.get("keep")), (None, 1))


if __name__''')

NAIVE_TESTS = TEST_EXISTING.replace('''

if __name__''', '''

    def test_overwrite(self):
        s = Store()
        s.put("a", 1)
        s.put("a", 2)
        s.restart()
        self.assertEqual(s.get("a"), 2)

    def test_many_puts_then_restart(self):
        s = Store()
        for i in range(300):
            s.put(f"k{i}", i)
        s.restart()
        self.assertEqual(s.get("k150"), 150)

    def test_put_many_rejects_bad_value(self):
        with self.assertRaises(TypeError):
            Store().put_many([("a", None)])


if __name__''')


def write_solution(r, which):
    with open(os.path.join(r, "tests", "test_store.py"), "w") as f:
        f.write(REFERENCE_TESTS if which == "reference" else NAIVE_TESTS)


TASK = {"skill": "correctness-first", "prompt": PROMPT, "prompt_none": PROMPT_NONE, "setup": setup, "user": user,
        "grade": grade, "turn_timeout": 300, "prereg": PREREG, "files": [__file__], "write_solution": write_solution}
