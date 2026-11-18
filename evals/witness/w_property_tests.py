"""Witnesses for skills/property-tests (and the shared 'lookup of the rows' claim in skills/tests-as-data).
Properties run over a seeded generator (stdlib random.Random) so results are identical everywhere."""
from __future__ import annotations

import calendar
import random

from core import witness


def _first_failure(prop, gen, n=500, seed=7):
    rng = random.Random(seed)
    for _ in range(n):
        x = gen(rng)
        if not prop(x):
            return True, f"counterexample {x!r}"
    return False, f"{n} generated cases passed"


def _examples_fail(fn, rows):
    bad = [(i, e, fn(i)) for i, e in rows if fn(i) != e]
    return (bool(bad), f"failing rows {bad}" if bad else f"all {len(rows)} example rows pass")


# --------------------------------------------------------------------------- the dumbest implementation: a lookup of the rows
LEAP_ROWS = [(2000, True), (1900, False), (2024, True), (2023, False)]
LOOKUP = dict(LEAP_ROWS)


def leap_lookup(y):           # the dumbest implementation that passes the rows (always constructible from any example table)
    return LOOKUP.get(y, False)


def _leap_properties(fn):
    """Properties that do not re-implement the rule: period 400, and 97 leap years in every 400-year window."""
    def prop(y):
        return fn(y) == fn(y + 400) and sum(fn(z) for z in range(y, y + 400)) == 97
    return _first_failure(prop, lambda r: r.randint(1, 3000), n=60)


witness(
    "pt-lookup-of-rows", "property-tests",
    "For ANY example table, a lookup of its rows passes it, so the table cannot tell a real implementation from a cheat; "
    "two properties that do not restate the rule (period 400; 97 leap years per 400) kill the lookup; calendar.isleap passes.",
    ignored=lambda: _examples_fail(leap_lookup, LEAP_ROWS),
    followed=lambda: _leap_properties(leap_lookup),
    fixed=lambda: _leap_properties(calendar.isleap),
)


# --------------------------------------------------------------------------- round-trip with edge-case generators
def enc_naive(fields):
    return ",".join(fields)


def dec_naive(s):
    return s.split(",") if s else []


def enc_ok(fields):          # length-prefixed: total over every list of strings
    return "".join(f"{len(f)}:{f}" for f in fields)


def dec_ok(s):
    out, i = [], 0
    while i < len(s):
        j = s.index(":", i)
        n = int(s[i:j])
        out.append(s[j + 1:j + 1 + n])
        i = j + 1 + n
    return out


ENC_ROWS = [(["a", "b"], "a,b"), (["hello"], "hello"), ([], "")]
EDGE_CHARS = ["", ",", ":", "\\", '"', "\n", "é", "😀", "a", "1"]


def _gen_fields(r):
    return ["".join(r.choice(EDGE_CHARS) for _ in range(r.randint(0, 4))) for _ in range(r.randint(0, 4))]


witness(
    "pt-round-trip", "property-tests",
    "A comma-join codec passes its example rows; decode(encode(x)) == x over generated field lists that include the "
    "delimiter, empty fields and unicode finds the break; a length-prefixed codec passes.",
    ignored=lambda: _examples_fail(enc_naive, ENC_ROWS),
    followed=lambda: _first_failure(lambda x: dec_naive(enc_naive(x)) == x, _gen_fields),
    fixed=lambda: _first_failure(lambda x: dec_ok(enc_ok(x)) == x, _gen_fields),
)


# --------------------------------------------------------------------------- edge-biased generators, not only uniform random
def dedupe_bug(xs):          # keeps the LAST occurrence: only differs from correct when a value repeats
    return [x for i, x in enumerate(xs) if x not in xs[i + 1:]]


def dedupe_ok(xs):
    return list(dict.fromkeys(xs))


def _dedupe_prop(fn):
    return lambda xs: fn(xs) == sorted(set(xs), key=xs.index)   # oracle: first occurrences, in order


def _uniform(r):
    return [r.randint(-10**9, 10**9) for _ in range(r.randint(1, 20))]


def _edge(r):
    kind = r.randrange(4)
    if kind == 0:
        return []
    if kind == 1:
        return [r.randint(0, 1)] * r.randint(1, 6)                   # all the same
    return [r.randint(0, 3) for _ in range(r.randint(1, 12))]        # tiny range: duplicates


witness(
    "pt-edge-generators", "property-tests",
    "A dedupe that keeps the last occurrence passes 2,000 uniformly random lists (duplicates never occur); a generator "
    "aimed at edge cases (empty, all-same, tiny value range) finds it at once; the correct dedupe passes it.",
    ignored=lambda: _first_failure(_dedupe_prop(dedupe_bug), _uniform, n=2000),
    followed=lambda: _first_failure(_dedupe_prop(dedupe_bug), _edge),
    fixed=lambda: _first_failure(_dedupe_prop(dedupe_ok), _edge),
)


# --------------------------------------------------------------------------- idempotence: the same event twice gives the same state
def apply_bug(state, ev):
    state = dict(state)
    state["total"] = state.get("total", 0) + ev["amount"]
    return state


def apply_ok(state, ev):
    if ev["id"] in state.get("seen", ()):
        return state
    return {"total": state.get("total", 0) + ev["amount"], "seen": (*state.get("seen", ()), ev["id"])}


def _fold(fn, evs):
    s: dict = {}
    for e in evs:
        s = fn(s, e)
    return s.get("total", 0)


CONSUMER_ROWS = [([{"id": 1, "amount": 5}], 5), ([{"id": 1, "amount": 5}, {"id": 2, "amount": 7}], 12)]


def _consumer_rows_fail(fn):
    bad = [t for evs, t in CONSUMER_ROWS if _fold(fn, evs) != t]
    return (bool(bad), f"failing rows {bad}" if bad else f"all {len(CONSUMER_ROWS)} example rows pass")


def _redelivered(r):
    evs = [{"id": i, "amount": r.randint(1, 9)} for i in range(r.randint(1, 6))]
    return evs, evs + [r.choice(evs) for _ in range(r.randint(1, 3))]   # at-least-once delivery: duplicates


witness(
    "pt-idempotence", "property-tests",
    "A consumer that adds every delivery passes its example rows (each event delivered once); 'redelivering events "
    "gives the same state' over generated duplicate deliveries catches the double count; an id-deduping consumer passes.",
    ignored=lambda: _consumer_rows_fail(apply_bug),
    followed=lambda: _first_failure(lambda p: _fold(apply_bug, p[0]) == _fold(apply_bug, p[1]), _redelivered),
    fixed=lambda: _first_failure(lambda p: _fold(apply_ok, p[0]) == _fold(apply_ok, p[1]), _redelivered),
)
