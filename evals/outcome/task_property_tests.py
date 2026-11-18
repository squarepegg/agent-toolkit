"""Outcome task: does skills/property-tests make an agent write tests that catch subtle wrong implementations?

The agent is asked whether four example tests for a CSV row formatter/parser are good enough, and to improve them.
Grading reruns the agent's tests against the real code and against seven plausible wrong implementations (mutants),
each a one-line change that the four original examples cannot see. Each is only reached by an input a generator aimed
at edge cases produces (a single empty field, a trailing empty field, empty fields in the middle, whitespace, a field
with both a comma and a quote) or by checking the examples are not the whole behaviour (the lookup). No model grades anything.

The skill and control arms are told to use the skill (so it loads); the none arm gets the same prompt without that
sentence: told to use a skill that does not exist, the agent stalls (3 of 5 runs, 300 s, almost no output).

History: v1 (merge_intervals) hit CEILING (5/5 in every arm). v2 (an arbitrary 32-field truncation mutant, prompt without
the skill's name) hit FLOOR (0/5 in every arm; nobody tested rows that long, and the skill was read in 1 of 5 runs). v3
is this file. See evals/negative-results.md.
"""
import os
import re
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "harness-ab"))
from tasks import init_repo, unittest_outcomes  # noqa: E402

MUT_NAMES = ["empty_single", "trailing_empty", "strip", "comma_and_quote", "quote_only_comma", "empty_middle", "lookup"]
# Pre-registered (changing any of this changes the task hash and stales old ledger rows)
PREREG = {"n": 10, "alpha": 0.05, "ceiling": 0.8,
          "primary": ["passes_real", *[f"kills_{m}" for m in MUT_NAMES], "src_unchanged"]}

REAL = '''"""One CSV row: format a list of fields into a line, and parse a line back. No newlines inside a row."""


def format_row(fields):
    """Join fields with commas. A field is quoted (inner quotes doubled) if it contains a comma or a quote, and the
    only field of a row is quoted when it is empty, so that [] formats as "" and [""] as '""'."""
    if fields == [""]:
        return '""'
    out = []
    for f in fields:
        if "," in f or '"' in f:
            out.append('"' + f.replace('"', '""') + '"')
        else:
            out.append(f)
    return ",".join(out)


def parse_row(line):
    """Inverse of format_row: parse_row(format_row(x)) == x for every list x of strings without newlines."""
    if line == "":
        return []
    fields, cur, i, n, inq = [], [], 0, len(line), False
    while i < n:
        c = line[i]
        if inq:
            if c == '"':
                if i + 1 < n and line[i + 1] == '"':
                    cur.append('"')
                    i += 2
                    continue
                inq = False
            else:
                cur.append(c)
        elif c == '"':
            inq = True
        elif c == ",":
            fields.append("".join(cur))
            cur = []
        else:
            cur.append(c)
        i += 1
    fields.append("".join(cur))
    return fields
'''

KNOWN_FMT = ('def format_row(fields):\n    known = {("a", "b"): "a,b", ("a,b", "c"): \'"a,b",c\'}\n'
             '    return known.get(tuple(fields), ",".join(fields))\n')
KNOWN_PARSE = ('def parse_row(line):\n    known = {"a,b": ["a", "b"], \'"a,b",c\': ["a,b", "c"]}\n'
               '    return known.get(line, line.split(",") if line else [])\n')


def _sub(old, new):
    assert REAL.count(old) == 1, old
    return REAL.replace(old, new)


MUTANTS = {
    # [""] formats as "" (and so parses back as []): only the one-field-empty-row reaches it
    "empty_single": _sub('    if fields == [""]:\n        return \'""\'\n', ""),
    # a trailing empty field is lost: "a," -> ["a"]
    "trailing_empty": _sub('    fields.append("".join(cur))\n    return fields', '    if cur:\n        fields.append("".join(cur))\n    return fields'),
    # fields lose surrounding whitespace
    "strip": _sub('            fields.append("".join(cur))\n            cur = []', '            fields.append("".join(cur).strip())\n            cur = []'),
    # inner quotes are doubled only when the field has no comma: fields with BOTH a comma and a quote break
    "comma_and_quote": _sub("out.append('\"' + f.replace('\"', '\"\"') + '\"')", "out.append('\"' + (f if \",\" in f else f.replace('\"', '\"\"')) + '\"')"),
    # fields with a quote but no comma are not quoted
    "quote_only_comma": _sub('if "," in f or \'"\' in f:', 'if "," in f:'),
    # empty fields before the last are lost: "a,,b" -> ["a", "b"], ",a" -> ["a"]
    "empty_middle": _sub('        elif c == ",":\n            fields.append("".join(cur))\n            cur = []', '        elif c == ",":\n            if cur:\n                fields.append("".join(cur))\n            cur = []'),
    # the dumbest implementation that passes the four examples: a lookup of them, naive join/split otherwise
    "lookup": '"""lookup of the examples"""\n\n\n' + KNOWN_FMT + "\n\n" + KNOWN_PARSE,
}
assert all(m != REAL for m in MUTANTS.values()) and set(MUTANTS) == set(MUT_NAMES)

TEST_EXISTING = '''import unittest
from csvline import format_row, parse_row


class CsvRowTest(unittest.TestCase):
    def test_format_plain(self):
        self.assertEqual(format_row(["a", "b"]), "a,b")

    def test_format_quotes_commas(self):
        self.assertEqual(format_row(["a,b", "c"]), '"a,b",c')

    def test_parse_plain(self):
        self.assertEqual(parse_row("a,b"), ["a", "b"])

    def test_parse_quoted(self):
        self.assertEqual(parse_row('"a,b",c'), ["a,b", "c"])


if __name__ == "__main__":
    unittest.main()
'''

SKILL_SENTENCE = "Use the property-tests-from-examples skill. "
PROMPT_NONE = ("Are the tests in tests/test_csvline.py good enough? Improve them. "
               "This is a test-only task: do not change "
          "csvline.py. No third-party packages are installed; tests run with "
          "`python3 -m unittest discover -s tests -t .`. Tell me what you found.")
PROMPT = SKILL_SENTENCE + PROMPT_NONE   # skill and control arms; the none arm gets PROMPT_NONE (a skill that is not there stalls the agent)


def setup(r):
    init_repo(r, {"csvline.py": REAL, "tests/__init__.py": "", "tests/test_csvline.py": TEST_EXISTING,
                  "README.md": "csvline. Tests: python3 -m unittest discover -s tests -t .\n"})


def user(turn, text):
    if turn >= 3 or not re.search(r"\?|permission|approve|confirm|let me know|your call", text, re.I):
        return None
    return "Go with your recommendation and finish without asking me again."


def _outcomes_with(r, src):
    d = tempfile.mkdtemp(prefix="pt-grade-")
    try:
        shutil.copytree(os.path.join(r, "tests"), os.path.join(d, "tests"))
        with open(os.path.join(d, "csvline.py"), "w") as f:
            f.write(src)
        return unittest_outcomes(d, timeout=300)
    finally:
        shutil.rmtree(d, ignore_errors=True)


def _green(out):
    return bool(out) and all(v in ("ok", "skipped", "expected") for v in out.values())


def grade(r, final_text, all_text, bash_cmds):
    real = _outcomes_with(r, REAL)
    checks = {"passes_real": _green(real)}
    detail = {"real": real}
    for name, src in MUTANTS.items():
        o = _outcomes_with(r, src)
        checks[f"kills_{name}"] = not _green(o)
        detail[name] = {k: v for k, v in o.items() if v != "ok"}
    with open(os.path.join(r, "csvline.py")) as f:
        checks["src_unchanged"] = f.read() == REAL
    checks["names_dumb_implementation"] = bool(re.search(r"hard.?cod|dumb|lookup|cheat", all_text, re.I))   # secondary
    return checks, detail


# --------------------------------------------------------------------------- grader fixtures (CI pins the grader both ways)
REFERENCE_TESTS = TEST_EXISTING.replace('''

if __name__''', '''

    def test_round_trip_generated(self):
        import random
        rng = random.Random(5)
        alphabet = ["", "a", "b", " ", ",", '"', "é", "1"]
        for _ in range(600):
            size = rng.choice([0, 1, 1, 2, 3, 5, 33, 40])
            fields = ["".join(rng.choice(alphabet) for _ in range(rng.choice([0, 0, 1, 2, 4]))) for _ in range(size)]
            self.assertEqual(parse_row(format_row(fields)), fields, fields)


if __name__''')

NAIVE_TESTS = TEST_EXISTING.replace('''

if __name__''', '''

    def test_single_field(self):
        self.assertEqual(parse_row(format_row(["hello"])), ["hello"])

    def test_empty_row(self):
        self.assertEqual(parse_row(format_row([])), [])

    def test_round_trip_examples(self):
        for fields in (["a", "b", "c"], ["x,y", "z"], ["1", "2"]):
            self.assertEqual(parse_row(format_row(fields)), fields)


if __name__''')


def write_solution(r, which):
    with open(os.path.join(r, "tests", "test_csvline.py"), "w") as f:
        f.write(REFERENCE_TESTS if which == "reference" else NAIVE_TESTS)


TASK = {"skill": "property-tests", "prompt": PROMPT, "prompt_none": PROMPT_NONE, "setup": setup, "user": user, "grade": grade, "turn_timeout": 300,
        "prereg": PREREG, "files": [__file__], "write_solution": write_solution}
