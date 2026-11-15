"""Outcome task: does skills/correctness-first make an agent write a check that catches a lost update?

bank.py splits a transfer into prepare() and commit(); several prepares may be outstanding. commit() writes the
balances computed at prepare time, so two interleaved transfers lose an update and create money. Every single
transfer looks right. "Detected" = some test of the agent's fails on bank.py and every test passes on the fixed bank.
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "harness-ab"))
from tasks import init_repo, unittest_outcomes  # noqa: E402

PREREG = {"n": 10, "alpha": 0.05, "ceiling": 0.8, "primary": ["detects_lost_update", "no_false_alarm_on_fixed", "src_unchanged"]}

BANK = '''"""In-memory bank. Request handlers run concurrently: each calls prepare() when a transfer request arrives and
commit() once the fraud check for it has passed, so many prepared transfers can be outstanding at once."""


class Pending:
    def __init__(self, bank, src, new_src, dst, new_dst):
        self.bank, self.src, self.new_src, self.dst, self.new_dst = bank, src, new_src, dst, new_dst

    def commit(self):
        """Apply the transfer. Returns True."""
        self.bank.balances[self.src] = self.new_src
        self.bank.balances[self.dst] = self.new_dst
        return True


class Bank:
    def __init__(self, balances):
        self.balances = dict(balances)

    def prepare(self, src, dst, amount):
        """Check funds and plan the transfer. Returns a Pending, or None if it is refused."""
        if src == dst or amount <= 0 or self.balances[src] < amount:
            return None
        return Pending(self, src, self.balances[src] - amount, dst, self.balances[dst] + amount)

    def balance(self, account):
        return self.balances[account]
'''

BANK_FIXED = BANK.replace(
    '''    def __init__(self, bank, src, new_src, dst, new_dst):
        self.bank, self.src, self.new_src, self.dst, self.new_dst = bank, src, new_src, dst, new_dst

    def commit(self):
        """Apply the transfer. Returns True."""
        self.bank.balances[self.src] = self.new_src
        self.bank.balances[self.dst] = self.new_dst
        return True''',
    '''    def __init__(self, bank, src, new_src, dst, new_dst):
        self.bank, self.src, self.dst = bank, src, dst
        self.amount = bank.balances[src] - new_src

    def commit(self):
        """Apply the transfer. Returns True (False if funds are no longer there; nothing changes then)."""
        b = self.bank.balances
        if b[self.src] < self.amount:
            return False
        b[self.src] -= self.amount
        b[self.dst] += self.amount
        return True''')
assert BANK_FIXED != BANK

TEST_EXISTING = '''import unittest
from bank import Bank


class BankTest(unittest.TestCase):
    def test_transfer(self):
        b = Bank({"a": 100, "b": 0})
        b.prepare("a", "b", 30).commit()
        self.assertEqual((b.balance("a"), b.balance("b")), (70, 30))

    def test_insufficient(self):
        self.assertIsNone(Bank({"a": 10, "b": 0}).prepare("a", "b", 30))


if __name__ == "__main__":
    unittest.main()
'''

SKILL_SENTENCE = "Use the correctness-first-design skill. "
PROMPT_NONE = ("bank.py is about to ship. Add tests under tests/ that would tell us whether it is correct. This is a "
               "test-only task: do not change bank.py. Stdlib only; tests run with "
               "`python3 -m unittest discover -s tests -t .`. Tell me what you found.")
PROMPT = SKILL_SENTENCE + PROMPT_NONE   # skill and control arms; the none arm gets PROMPT_NONE (a missing skill stalls the agent)


def setup(r):
    init_repo(r, {"bank.py": BANK, "tests/__init__.py": "", "tests/test_bank.py": TEST_EXISTING,
                  "README.md": "bank. Tests: python3 -m unittest discover -s tests -t .\n"})


def user(turn, text):
    if turn >= 3 or not re.search(r"\?|permission|approve|confirm|let me know|your call", text, re.I):
        return None
    return "Go with your recommendation and finish without asking me again."


def _with(r, src):
    d = tempfile.mkdtemp(prefix="cf-grade-")
    try:
        shutil.copytree(os.path.join(r, "tests"), os.path.join(d, "tests"))
        with open(os.path.join(d, "bank.py"), "w") as f:
            f.write(src)
        return unittest_outcomes(d, timeout=300)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _green(out):
    return bool(out) and all(v in ("ok", "skipped", "expected") for v in out.values())


def grade(r, final_text, all_text, bash_cmds):
    buggy, fixed = _with(r, BANK), _with(r, BANK_FIXED)
    low = all_text.lower()
    checks = {
        "detects_lost_update": not _green(buggy) and "__crash__" not in buggy and "__timeout__" not in buggy,
        "no_false_alarm_on_fixed": _green(fixed),
        "src_unchanged": open(os.path.join(r, "bank.py")).read() == BANK,
        "names_conservation": bool(re.search(r"conserv|total (is |stays )?(constant|unchanged)|money (is )?(created|lost)|sum of", low)),
        "names_gray_failure": bool(re.search(r"gr[ae]y.?failure", low)),
    }
    return checks, {"buggy": {k: v for k, v in buggy.items() if v != "ok"}, "fixed": {k: v for k, v in fixed.items() if v != "ok"}}


REFERENCE_TESTS = TEST_EXISTING.replace('''

if __name__''', '''

    def test_workload_conserves_total(self):
        import random
        for seed in range(50):
            rng = random.Random(seed)
            b = Bank({k: 100 for k in "abcd"})
            pending = []
            for _ in range(30):
                if pending and rng.random() < 0.5:
                    pending.pop(rng.randrange(len(pending))).commit()
                else:
                    p = b.prepare(rng.choice("abcd"), rng.choice("abcd"), rng.randint(1, 60))
                    if p:
                        pending.append(p)
                self.assertTrue(all(b.balance(k) >= 0 for k in "abcd"), seed)
            for p in pending:
                p.commit()
            self.assertEqual(sum(b.balance(k) for k in "abcd"), 400, f"seed {seed}")


if __name__''')

NAIVE_TESTS = TEST_EXISTING.replace('''

if __name__''', '''

    def test_two_transfers_in_a_row(self):
        b = Bank({"a": 100, "b": 0})
        b.prepare("a", "b", 30).commit()
        b.prepare("a", "b", 30).commit()
        self.assertEqual(b.balance("a"), 40)


if __name__''')


def write_solution(r, which):
    with open(os.path.join(r, "tests", "test_bank.py"), "w") as f:
        f.write(REFERENCE_TESTS if which == "reference" else NAIVE_TESTS)


TASK = {"skill": "correctness-first", "prompt": PROMPT, "prompt_none": PROMPT_NONE, "setup": setup, "user": user, "grade": grade, "turn_timeout": 300,
        "prereg": PREREG, "files": [__file__], "write_solution": write_solution}
