"""Outcome task (decision score): does skills/spec-first make an agent surface the decisions a short request leaves open?

The agent is asked to add `snooze(reminder, minutes, now)` to a small reminders module, to interview the user first (one
question at a time) about anything the code cannot tell it, and then to build it. The scripted user holds six decisions the
code cannot settle and gives each one only when the agent asks about that topic (generous keyword patterns; a question that
matches nothing gets "use your best judgment"). Grading runs the agent's code against six hidden behaviours, one per decision:
no model grades anything. Score = decisions the final code honours (0..6), or 0 if the existing tests were edited or the
suite is red. Four of the six are choices a model rarely guesses (limit of 3 with SnoozeLimit, the count persisted under
"snoozes", completed reminders return False, minutes clamped to 60 with ValueError at 0); two are the common default
(count from now) and a return value, so a no-skill agent is not stuck at zero.

History: v1 (smoke, 1 run per arm, discarded) showed two task faults: the scripted user matched words in the agent's own restatement of
the task ("minutes", "return"), so it handed out answers nobody asked for, and the skill arm stopped after writing the spec (the skill
says "hand off"), so its score was 0 for the wrong reason. v2 (this file) answers only question sentences and nudges a spec-only stop.

Design choice: every arm is asked to interview first, so the comparison is the skill's interview against a plain model's
interview (and the length-matched control), not "interview vs never ask". The skill and control arms are told to use the
skill (so it loads); the none arm gets the same prompt without that sentence.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "harness-ab"))
from tasks import init_repo, unittest_outcomes  # noqa: E402

DECISIONS = ["r1_from_now", "r2_limit", "r3_persist", "r4_done", "r5_bounds", "r6_returns"]
# Pre-registered (changing any of this changes the task hash and stales old ledger rows)
PREREG = {"n": 5, "alpha": 0.05, "ceiling": 0.8,
          "score": {"kills": DECISIONS, "gate": ["suite_green", "tests_unchanged"]},
          "primary": ["suite_green", "tests_unchanged", *DECISIONS]}

REAL = '''"""Reminders for a small CLI app. Times are naive datetimes."""
import json
from datetime import datetime


class Reminder:
    def __init__(self, id, text, due, done=False):
        self.id, self.text, self.due, self.done = id, text, due, done

    def to_dict(self):
        return {"id": self.id, "text": self.text, "due": self.due.isoformat(), "done": self.done}

    @classmethod
    def from_dict(cls, d):
        return cls(d["id"], d["text"], datetime.fromisoformat(d["due"]), d.get("done", False))


def save(path, reminders):
    with open(path, "w") as f:
        json.dump({"reminders": [r.to_dict() for r in reminders]}, f)


def load(path):
    with open(path) as f:
        return [Reminder.from_dict(d) for d in json.load(f)["reminders"]]


def due_now(reminders, now):
    """Reminders that are not done and due at or before now, earliest first."""
    return sorted((r for r in reminders if not r.done and r.due <= now), key=lambda r: r.due)
'''

TEST_EXISTING = '''import os
import tempfile
import unittest
from datetime import datetime

from reminders import Reminder, due_now, load, save

NOW = datetime(2026, 1, 1, 9, 0)


class RemindersTest(unittest.TestCase):
    def test_due_now_is_sorted(self):
        a, b = Reminder(1, "a", datetime(2026, 1, 1, 8, 0)), Reminder(2, "b", datetime(2026, 1, 1, 7, 0))
        self.assertEqual([r.id for r in due_now([a, b], NOW)], [2, 1])

    def test_done_and_future_are_not_due(self):
        done = Reminder(1, "a", datetime(2026, 1, 1, 8, 0), done=True)
        later = Reminder(2, "b", datetime(2026, 1, 1, 10, 0))
        self.assertEqual(due_now([done, later], NOW), [])

    def test_save_load_round_trip(self):
        p = os.path.join(tempfile.mkdtemp(), "r.json")
        save(p, [Reminder(1, "a", datetime(2026, 1, 1, 8, 0))])
        self.assertEqual([(r.id, r.text, r.due, r.done) for r in load(p)], [(1, "a", datetime(2026, 1, 1, 8, 0), False)])


if __name__ == "__main__":
    unittest.main()
'''

SKILL_SENTENCE = "Use the spec-first skill. "
PROMPT_NONE = ("Users keep asking to snooze reminders. Add `snooze(reminder, minutes, now)` to reminders.py: it pushes a reminder's "
               "due time later (`now` is a datetime the caller passes in). Interview me first, one question at a time, about anything "
               "the code cannot tell you, then build it in this session. Put new tests in tests/test_snooze.py and do not modify "
               "tests/test_reminders.py. No third-party packages are installed; tests run with "
               "`python3 -m unittest discover -s tests -t .`. Tell me what you decided.")
PROMPT = SKILL_SENTENCE + PROMPT_NONE   # skill and control arms; the none arm gets PROMPT_NONE

# --------------------------------------------------------------------------- the scripted user
# (decision id, pattern on the agent's message, the answer). Every matching topic is answered in one reply.
ANSWERS = [
    ("r1", r"from now|from the (original|current|due)|relative to|counted from|measured from|based on|starting (from|at)|new due|push(es)? (it )?(back|later)",
     "Count from now: the new due time is `now + minutes`."),
    ("r2", r"how many times|at most|max(imum)? (number|count) of (times|snoozes)|max(imum)? (times|snoozes)|repeat|again and again|snooze (it )?(again|twice)|unlimited|forever|indefinite|"
           r"\bcap\b|\blimit\b|too many|abuse",
     "At most 3 snoozes per reminder. The 4th attempt raises `SnoozeLimit` (an Exception subclass defined in reminders.py) and leaves the reminder unchanged."),
    ("r3", r"persist|\bsaved?\b|storage|restart|reload|json|on disk|data model|stored?\b|remember|keep track|track(ing)? (of )?(how|the)",
     'The snooze count must survive save/load: store it under the JSON key "snoozes". A saved file without that key means 0.'),
    ("r4", r"complet|already done|\bdone\b|finished|marked",
     "Snoozing a completed reminder does nothing and returns False (no exception)."),
    ("r5", r"(range|negative|zero|upper|lower|maximum|minimum|too (long|large|big|small)|clamp|bounds|invalid|validat|edge cases?).{0,60}(minutes|duration|snooze|value)|"
           r"(minutes|duration|snooze (time|length)).{0,60}(range|negative|zero|upper|maximum|minimum|too (long|large|big)|clamp|bounds|invalid|validat|edge cases?)|how (long|far) (can|may|should)",
     "`minutes` above 60 is clamped to 60. Zero or negative `minutes` raises ValueError and changes nothing."),
    ("r6", r"return|result|what (should|does) (it|the function) (give|return)|response|output",
     "On success return the new due datetime."),
]
BUILT = re.compile(r"tests? (all )?pass|passed|\bOK\b|all \d+ tests|\bbuilt\b|implemented|added `?snooze", re.I)
NUDGE = "The spec is agreed. Do not hand off: build it now, in this session, with tests in tests/test_snooze.py."
ACCEPT = re.compile(r"(accept|approv|sign off|lock)\w*( this| the| my)? spec|spec (is )?(ok|good|accepted|ready)|ok to (build|proceed)|shall i (lock|accept|build|proceed)|ready to build|"
                    r"(look|looks) (right|good|correct)\??$|do you (accept|approve|confirm)", re.I | re.M)
MAX_TURNS = 16


def questions(text):
    """The sentences of a message that ask something (outside code blocks): context and option text restating the task is not a question."""
    t = re.sub(r"```.*?```", "", text, flags=re.S)
    return [x for x in re.split(r"(?<=[?.!])\s+|\n+", t) if "?" in x]


def answers(text):
    """The decisions the agent's questions ask about -> the user's reply lines, in a fixed order."""
    q = " ".join(questions(text))
    return [a for _, p, a in ANSWERS if re.search(p, q, re.I)]


def user(turn, text):
    if turn >= MAX_TURNS:
        return None
    if "?" not in text:   # nothing asked: either finished, or stopped after writing the spec (the skill says "hand off"; this task builds here)
        return NUDGE if re.search(r"\bspec\b|specs/", text, re.I) and not BUILT.search(text) else None
    if ACCEPT.search(text):
        return "Accepted. Build it now, in this session, with tests in tests/test_snooze.py."
    got = answers(text)
    if got:
        return " ".join(got) + " Anything else you need to know before you build?"
    return "Go with your best judgment and finish without asking me again."


# --------------------------------------------------------------------------- grader
CHECKER = r'''
import json, os, tempfile
from datetime import datetime, timedelta
import reminders as m

NOW = datetime(2026, 1, 1, 9, 0)
DUE = datetime(2026, 1, 1, 8, 30)
out = {}


def fresh(done=False):
    r = m.Reminder(1, "x", DUE)
    r.done = done
    return r


def check(name, fn):
    try:
        out[name] = bool(fn())
    except BaseException:
        out[name] = False


def r1():
    r = fresh(); m.snooze(r, 10, NOW)
    return r.due == NOW + timedelta(minutes=10)


def r2():
    r = fresh()
    for _ in range(3):
        m.snooze(r, 5, NOW)
    before = r.due
    try:
        m.snooze(r, 5, NOW)
    except m.SnoozeLimit as e:
        return isinstance(e, Exception) and r.due == before
    return False


def r3():
    d = tempfile.mkdtemp()
    r = fresh(); m.snooze(r, 5, NOW); m.snooze(r, 5, NOW)
    p, q = os.path.join(d, "f.json"), os.path.join(d, "g.json")
    m.save(p, [r])
    if json.load(open(p))["reminders"][0].get("snoozes") != 2:
        return False
    m.save(q, m.load(p))
    if json.load(open(q))["reminders"][0].get("snoozes") != 2:
        return False
    json.dump({"reminders": [{"id": 2, "text": "y", "due": "2026-01-01T08:00:00", "done": False}]}, open(p, "w"))
    old = m.load(p)[0]
    m.snooze(old, 5, NOW)
    m.save(q, [old])
    return json.load(open(q))["reminders"][0].get("snoozes") == 1


def r4():
    r = fresh(done=True); before = r.due
    return m.snooze(r, 10, NOW) is False and r.due == before


def r5():
    r = fresh(); m.snooze(r, 120, NOW)
    if r.due != NOW + timedelta(minutes=60):
        return False
    for bad in (0, -5):
        r = fresh(); before = r.due
        try:
            m.snooze(r, bad, NOW)
        except ValueError:
            if r.due != before:
                return False
        else:
            return False
    return True


def r6():
    r = fresh()
    return m.snooze(r, 10, NOW) == NOW + timedelta(minutes=10)


for name, fn in (("r1_from_now", r1), ("r2_limit", r2), ("r3_persist", r3), ("r4_done", r4), ("r5_bounds", r5), ("r6_returns", r6)):
    check(name, fn)
print("RESULT " + json.dumps(out))
'''


def setup(r):
    init_repo(r, {"reminders.py": REAL, "tests/__init__.py": "", "tests/test_reminders.py": TEST_EXISTING,
                  "README.md": "reminders. Tests: python3 -m unittest discover -s tests -t .\n"})


def _behaviours(r):
    try:
        p = subprocess.run([sys.executable, "-c", CHECKER], cwd=r, env={**os.environ, "PYTHONPATH": r}, capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return {k: False for k in DECISIONS}
    m = re.search(r"^RESULT (.*)$", p.stdout, re.M)
    got = json.loads(m.group(1)) if m else {}
    return {k: bool(got.get(k)) for k in DECISIONS}


def _green(out):
    return bool(out) and all(v in ("ok", "skipped", "expected") for v in out.values())


def grade(r, final_text, all_text, bash_cmds):
    with open(os.path.join(r, "tests", "test_reminders.py")) as f:
        unchanged = f.read() == TEST_EXISTING
    suite = unittest_outcomes(r, timeout=300)
    checks = {"suite_green": _green(suite), "tests_unchanged": unchanged, **_behaviours(r)}
    asked = len(re.findall(r"\?", re.sub(r"```.*?```", "", all_text, flags=re.S)))
    checks["asked_questions"] = asked > 0   # secondary (not in the score)
    checks["wrote_spec"] = os.path.isdir(os.path.join(r, "specs")) or bool(re.search(r"specs/\S+\.md", all_text))   # secondary
    return checks, {"suite": {k: v for k, v in suite.items() if v != "ok"}, "questions": asked}


# --------------------------------------------------------------------------- grader fixtures (CI pins the grader both ways)
REFERENCE = REAL.replace("from datetime import datetime\n", "from datetime import datetime, timedelta\n\nMAX_SNOOZES, MAX_MINUTES = 3, 60\n\n\nclass SnoozeLimit(Exception):\n    pass\n") \
    .replace("def __init__(self, id, text, due, done=False):\n        self.id, self.text, self.due, self.done = id, text, due, done",
             "def __init__(self, id, text, due, done=False, snoozes=0):\n        self.id, self.text, self.due, self.done, self.snoozes = id, text, due, done, snoozes") \
    .replace('"done": self.done}', '"done": self.done, "snoozes": self.snoozes}') \
    .replace('d.get("done", False))', 'd.get("done", False), d.get("snoozes", 0))') \
    + '''

def snooze(reminder, minutes, now):
    """Push the due time to now + minutes (clamped to 60). At most 3 snoozes; a completed reminder is left alone."""
    if reminder.done:
        return False
    if minutes <= 0:
        raise ValueError("minutes must be positive")
    if reminder.snoozes >= MAX_SNOOZES:
        raise SnoozeLimit(f"reminder {reminder.id} was already snoozed {MAX_SNOOZES} times")
    reminder.due = now + timedelta(minutes=min(minutes, MAX_MINUTES))
    reminder.snoozes += 1
    return reminder.due
'''

NAIVE = REAL.replace("from datetime import datetime\n", "from datetime import datetime, timedelta\n") + '''

def snooze(reminder, minutes, now):
    reminder.due = now + timedelta(minutes=minutes)
'''

SNOOZE_TEST = '''import unittest
from datetime import datetime

from reminders import Reminder, snooze


class SnoozeTest(unittest.TestCase):
    def test_pushes_due_time(self):
        r = Reminder(1, "a", datetime(2026, 1, 1, 8, 0))
        snooze(r, 10, datetime(2026, 1, 1, 9, 0))
        self.assertEqual(r.due, datetime(2026, 1, 1, 9, 10))


if __name__ == "__main__":
    unittest.main()
'''


def write_solution(r, which):
    with open(os.path.join(r, "reminders.py"), "w") as f:
        f.write(REFERENCE if which == "reference" else NAIVE)
    with open(os.path.join(r, "tests", "test_snooze.py"), "w") as f:
        f.write(SNOOZE_TEST)


TASK = {"skill": "spec-first", "prompt": PROMPT, "prompt_none": PROMPT_NONE, "setup": setup, "user": user, "grade": grade, "turn_timeout": 300,
        "prereg": PREREG, "files": [__file__], "write_solution": write_solution}
