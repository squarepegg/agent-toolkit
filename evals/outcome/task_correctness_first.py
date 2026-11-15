"""Outcome task: does skills/correctness-first make an agent write a workload that finds a bug only long runs reach?

store.py is a small log-structured key-value store: operations go to segments of 128 entries, and when more than four
sealed segments exist, two of them are merged. restart() rebuilds the in-memory index from the segments. The bug: the
merge takes the two NEWEST sealed segments and drops tombstones, so a key put early (an old segment) and deleted later
(a merged segment) comes back after restart(). Reaching it takes 640+ operations, a delete of an old key, and a
restart: a few hand-picked examples never get there; a model-based workload (random ops against a dict, with
restarts, long enough) does. "Detected" = some test of the agent's fails on store.py, and every test passes on the
fixed store.

v1 of this task (a lost update in bank.py, visible by reading the code) hit CEILING in the no-skill arm (4/4); see
evals/negative-results.md.
"""
import os
import random
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "harness-ab"))
from tasks import init_repo, unittest_outcomes  # noqa: E402

# Pre-registered (changing any of this changes the task hash and stales old ledger rows)
PREREG = {"n": 10, "alpha": 0.05, "ceiling": 0.8, "primary": ["detects_bug", "no_false_alarm_on_fixed", "src_unchanged"]}

STORE = '''"""A small log-structured key-value store.

Writes append to the newest segment of the log. A segment holds 128 entries; when it is full a new one is started, and
when more than four full (sealed) segments exist, two of them are merged into one to bound the log. restart()
simulates a process restart: the in-memory index is thrown away and rebuilt by replaying every segment in order. After
restart() the store must behave exactly as it did before.
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
        """Merge two sealed segments into one, keeping only the live puts."""
        i = len(self.segments) - 4
        live = {}
        for op, key, value in self.segments[i] + self.segments[i + 1]:
            if op == "put":
                live[key] = value
            else:
                live.pop(key, None)
        self.segments[i:i + 2] = [[("put", k, v) for k, v in live.items()]]
'''

STORE_FIXED = STORE.replace("        i = len(self.segments) - 4\n", "        i = 0   # the two OLDEST sealed segments: nothing older can hold a put that a dropped tombstone was hiding\n")
assert STORE_FIXED != STORE

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
        s.delete("b")
        s.restart()
        self.assertEqual((s.get("a"), s.get("b")), (1, None))


if __name__ == "__main__":
    unittest.main()
'''

SKILL_SENTENCE = "Use the correctness-first-design skill. "
PROMPT_NONE = ("store.py is about to ship. Add tests under tests/ that would tell us whether it is correct. This is a "
               "test-only task: do not change store.py. Stdlib only; tests run with "
               "`python3 -m unittest discover -s tests -t .`. Tell me what you found.")
PROMPT = SKILL_SENTENCE + PROMPT_NONE   # skill and control arms; the none arm gets PROMPT_NONE (a missing skill stalls the agent)


def setup(r):
    init_repo(r, {"store.py": STORE, "tests/__init__.py": "", "tests/test_store.py": TEST_EXISTING,
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
        return unittest_outcomes(d, timeout=300)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _green(out):
    return bool(out) and all(v in ("ok", "skipped", "expected") for v in out.values())


def grade(r, final_text, all_text, bash_cmds):
    buggy, fixed = _with(r, STORE), _with(r, STORE_FIXED)
    low = all_text.lower()
    with open(os.path.join(r, "store.py")) as f:
        unchanged = f.read() == STORE
    checks = {
        "detects_bug": not _green(buggy) and "__crash__" not in buggy and "__timeout__" not in buggy,
        "no_false_alarm_on_fixed": _green(fixed),
        "src_unchanged": unchanged,
        # secondary (not in the verdict): the skill's vocabulary
        "names_workload": bool(re.search(r"model.based|oracle|reference model|differential|random(ized)? (op|work|seq)|workload", low)),
        "names_gray_failure": bool(re.search(r"gr[ae]y.?failure", low)),
    }
    return checks, {"buggy": {k: v for k, v in buggy.items() if v != "ok"}, "fixed": {k: v for k, v in fixed.items() if v != "ok"}}


# --------------------------------------------------------------------------- grader fixtures (CI pins the grader both ways)
REFERENCE_TESTS = TEST_EXISTING.replace('''

if __name__''', '''

    def test_model_based_workload(self):
        import random
        for seed in range(20):
            rng = random.Random(seed)
            s, model = Store(), {}
            for i in range(1500):
                op = rng.choice(["put", "put", "delete", "get", "restart"] if i > 5 else ["put"])
                k = f"k{rng.randrange(150)}"
                if op == "put":
                    s.put(k, i)
                    model[k] = i
                elif op == "delete":
                    s.delete(k)
                    model.pop(k, None)
                elif op == "restart" and rng.random() < 0.1:
                    s.restart()
                for key in (k, f"k{rng.randrange(150)}"):
                    self.assertEqual(s.get(key), model.get(key), (seed, i, key))
            s.restart()
            self.assertEqual({f"k{j}": s.get(f"k{j}") for j in range(150)}, {f"k{j}": model.get(f"k{j}") for j in range(150)}, seed)


if __name__''')

NAIVE_TESTS = TEST_EXISTING.replace('''

if __name__''', '''

    def test_overwrite(self):
        s = Store()
        s.put("a", 1)
        s.put("a", 2)
        self.assertEqual(s.get("a"), 2)

    def test_many_puts_then_restart(self):
        s = Store()
        for i in range(300):
            s.put(f"k{i}", i)
        s.restart()
        self.assertEqual(s.get("k150"), 150)


if __name__''')


def write_solution(r, which):
    with open(os.path.join(r, "tests", "test_store.py"), "w") as f:
        f.write(REFERENCE_TESTS if which == "reference" else NAIVE_TESTS)


TASK = {"skill": "correctness-first", "prompt": PROMPT, "prompt_none": PROMPT_NONE, "setup": setup, "user": user,
        "grade": grade, "turn_timeout": 300, "prereg": PREREG, "files": [__file__], "write_solution": write_solution}
