"""Witnesses for skills/dst. Each runs the skill's own Python engine (templates/python/dst.py) on a small real SUT."""
from __future__ import annotations

import hashlib
import os
import random
import sys

from core import HERE, ROOT, catches, witness
from dst import SimulationContext, known_bug, run_suite

sys.path.insert(0, os.path.join(ROOT, "evals", "harness-ab"))
from tasks import CACHE, CACHE_FIXED  # noqa: E402  the same race the live dst task plants


def _module(src: str) -> dict:
    ns: dict = {}
    exec(compile(src, "cache.py", "exec"), ns)
    return ns


# --------------------------------------------------------------------------- re-entrant hook (section 3, 4)
def _cache_scenario(mod: dict, hook: bool):
    """get / update / advance on two keys. With hook=True an update may land INSIDE store.read (after the read, before
    the cache stores it): the skill's `onInsideRead` re-entrant hook."""
    def scenario(sim: SimulationContext) -> str:
        truth: dict = {}
        cache_box: list = []

        class HookStore(mod["Store"]):
            def read(self, key):
                value = self.data.get(key)
                if hook and sim.chance(30):
                    v = f"mid{sim.step_count}"
                    sim.record_step(f"  inside read({key}): update -> {v}")
                    mod["update"](self, cache_box[0], key, v)
                    truth[key] = v
                    sim.sometimes("update_inside_read", True)
                return value

        store = HookStore()
        cache = mod["ReadThroughCache"](store, ttl_seconds=30.0, clock=lambda: sim.clock.ticker_nanos() / 1e9)
        cache_box.append(cache)
        for i in range(40):
            op, key = sim.choose(["get", "get", "update", "advance"]), sim.choose(["a", "b"])
            if op == "get":
                before = truth.get(key)
                got = cache.get(key)
                sim.record_step(f"op[{i}] get {key} -> {got}")
                # a read that overlapped an update may return either value; every later read must see the update
                sim.always("ReadAfterUpdate", got in (before, truth.get(key)),
                           lambda: f"got={got} truth={truth.get(key)}")
            elif op == "update":
                v = f"v{i}"
                mod["update"](store, cache, key, v)
                truth[key] = v
                sim.record_step(f"op[{i}] update {key}={v}")
            else:
                sim.clock.advance_millis(sim.next_int(40_000))
                sim.record_step(f"op[{i}] advance")
        return repr(sorted(truth.items()))
    return scenario


witness(
    "dst-reentrant-hook", "dst",
    "A race inside one call (update lands during store.read) is unreachable by sequential ops at ANY seed count; a "
    "re-entrant hook inside the dependency call finds it in the PR-tier 150 seeds; the fixed cache passes.",
    ignored=lambda: catches(lambda: run_suite("seq", set(), _cache_scenario(_module(CACHE), hook=False),
                                              default_seed_count=5000), "[ReadAfterUpdate]"),
    followed=lambda: catches(lambda: run_suite("hook", {"update_inside_read"}, _cache_scenario(_module(CACHE), hook=True)),
                             "[ReadAfterUpdate]"),
    fixed=lambda: catches(lambda: run_suite("hook", {"update_inside_read"}, _cache_scenario(_module(CACHE_FIXED), hook=True)),
                          "[ReadAfterUpdate]"),
)


# --------------------------------------------------------------------------- small key pools; seed count is not the lever; mutation check; sometimes guards a conditional always
class Registry:
    def __init__(self, overwrite_bug: bool):
        self.owner: dict[int, str] = {}
        self.bug = overwrite_bug

    def claim(self, key: int, who: str) -> str:
        if key in self.owner and not self.bug:
            return "taken"
        self.owner[key] = who
        return "ok"


def _registry_scenario(bug: bool, key_pool: int, guard: bool):
    def scenario(sim: SimulationContext) -> str:
        reg = Registry(bug)
        for i in range(40):
            key, who = sim.next_int(key_pool), f"c{sim.next_int(3)}"
            had = key in reg.owner
            res = reg.claim(key, who)
            sim.record_step(f"op[{i}] claim {key} by {who} -> {res}")
            if had:                                    # a conditional always: vacuous unless a collision happens
                if guard:
                    sim.sometimes("claim_collision", True)
                sim.always("NoDoubleClaim", res == "taken", lambda: f"key {key} re-claimed")
        return repr(sorted(reg.owner.items()))
    return scenario


witness(
    "dst-small-key-pool", "dst",
    "With a huge key space, `always(NoDoubleClaim)` sits in a branch that is never taken: an overwrite mutant survives "
    "10,000 seeds. Declaring a `sometimes` on the branch exposes the vacuity; a small key pool catches the mutant in 150 "
    "seeds; the correct registry passes.",
    ignored_more_seeds=lambda: catches(lambda: run_suite("big", set(), _registry_scenario(True, 10**12, False),
                                                         default_seed_count=10_000), "[NoDoubleClaim]"),
    extra_caught_vacuity_gate=lambda: catches(lambda: run_suite("big", {"claim_collision"},
                                                                _registry_scenario(True, 10**12, True)), "Vacuous simulation"),
    followed=lambda: catches(lambda: run_suite("small", {"claim_collision"}, _registry_scenario(True, 8, True)), "[NoDoubleClaim]"),
    fixed=lambda: catches(lambda: run_suite("small", {"claim_collision"}, _registry_scenario(False, 8, True)), "[NoDoubleClaim]"),
)


# --------------------------------------------------------------------------- a model protects the copy; conformance by coverage, not taste
class RealAccount:
    """'Production'. limit_bug=True is a deliberate break: a withdrawal over the cap is refused but still debited."""
    CAP = 990

    def __init__(self, limit_bug: bool):
        self.balance, self.bug = 500, limit_bug

    def apply(self, op: tuple[str, int]) -> str:
        kind, amt = op
        if kind == "deposit":
            self.balance += amt
            return "ok"
        if amt >= self.CAP:
            if self.bug:
                self.balance -= amt
            return "limit"
        if amt > self.balance:
            return "insufficient"
        self.balance -= amt
        return "ok"


class ModelAccount:
    """A hand-written model that copied production's logic (before the break)."""
    def __init__(self):
        self.balance = 500

    def apply(self, op):
        kind, amt = op
        if kind == "deposit":
            self.balance += amt
            return "ok"
        if amt >= RealAccount.CAP:
            return "limit"
        if amt > self.balance:
            return "insufficient"
        self.balance -= amt
        return "ok"


def _ops(seed: int, n: int = 20) -> list[tuple[str, int]]:
    """Ops generated UP FRONT from the seed (skill section 8): one list, replayed against model and real."""
    rng = random.Random(seed)
    return [(rng.choice(["deposit", "withdraw"]), rng.randint(1, 1000)) for _ in range(n)]


def _model_sweep():
    def scenario(sim: SimulationContext) -> str:
        m = ModelAccount()
        for op in _ops(sim.seed):
            res = m.apply(op)
            sim.record_step(f"{op} -> {res}")
            sim.always("BalanceNonNegative", m.balance >= 0, lambda: f"balance={m.balance}")
        return str(m.balance)
    return scenario


def _conformance(seeds: list[int], limit_bug: bool) -> None:
    for seed in seeds:
        m, r = ModelAccount(), RealAccount(limit_bug)
        for i, op in enumerate(_ops(seed)):
            a, b = m.apply(op), r.apply(op)
            assert (a, m.balance) == (b, r.balance), \
                f"conformance divergence seed={seed} op[{i}]={op}: model={(a, m.balance)} real={(b, r.balance)}"


def _answer_classes(seed: int) -> set[str]:
    m = ModelAccount()
    return {f"{op[0]}:{m.apply(op)}" for op in _ops(seed)}


def greedy_cover_seeds(candidates, must_reach):
    """Pick seeds until every op x answer class the model can produce is reached (the template's greedyCoverSeeds)."""
    picked, seen = [], set()
    for s in candidates:
        new = _answer_classes(s) - seen
        if new:
            picked.append(s)
            seen |= new
        if must_reach <= seen:
            break
    assert must_reach <= seen, f"candidates never reach {sorted(must_reach - seen)}"
    return picked


HAND_PICKED = [1, 2, 3, 7, 42]   # the dry run's sample, quoted in the skill
CLASSES = {"deposit:ok", "withdraw:ok", "withdraw:insufficient", "withdraw:limit"}


def _rare_share() -> tuple[bool, str]:
    hit = sum("withdraw:limit" in _answer_classes(s) for s in range(1, 1001))
    misses_hand = not any("withdraw:limit" in _answer_classes(s) for s in HAND_PICKED)
    return (misses_hand and 0 < hit < 200, f"rare answer class reached by {hit}/1000 seeds; hand-picked {HAND_PICKED} reach it: {not misses_hand}")


witness(
    "dst-conformance-coverage", "dst",
    "A sweep over a hand-written model stays green when production breaks (it protects the copy). Conformance on a "
    "hand-picked seed sample misses a break that only a rare answer class reaches; a coverage-chosen sample catches it; "
    "with production unbroken, conformance passes.",
    ignored_model_sweep=lambda: catches(lambda: run_suite("model", set(), _model_sweep()), "[BalanceNonNegative]"),
    ignored_hand_picked=lambda: catches(lambda: _conformance(HAND_PICKED, limit_bug=True), "conformance divergence"),
    extra_caught_hand_sample_misses_rare_class=_rare_share,
    followed=lambda: catches(lambda: _conformance(greedy_cover_seeds(range(1, 500), CLASSES), limit_bug=True),
                             "conformance divergence"),
    fixed=lambda: catches(lambda: _conformance(greedy_cover_seeds(range(1, 500), CLASSES), limit_bug=False),
                          "conformance divergence"),
)


# --------------------------------------------------------------------------- ghost state from the environment, never from the model
class Vendor:
    def __init__(self, sim: SimulationContext):
        self.sim, self.accepted = sim, []

    def send(self, msg: str) -> int:
        if self.sim.chance(20):
            self.sim.sometimes("throttled", True)
            return 429
        self.accepted.append(msg)            # what the environment ACTUALLY received
        return 200


class Notifier:
    def __init__(self, vendor, treat_429_as_ok: bool):
        self.vendor, self.bug, self.delivered = vendor, treat_429_as_ok, set()

    def send_all(self, msgs):
        for m in msgs:
            for _ in range(10):
                code = self.vendor.send(m)
                if code == 200 or (self.bug and code == 429):
                    self.delivered.add(m)    # the SUT's own interpretation
                    break


def _ghost(bug: bool, from_env: bool):
    def scenario(sim: SimulationContext) -> str:
        vendor = Vendor(sim)
        n = Notifier(vendor, bug)
        msgs = [f"m{i}" for i in range(sim.next_int(8) + 3)]
        n.send_all(msgs)
        sim.record_step(f"sent {len(msgs)}")
        ghost = set(vendor.accepted) if from_env else n.delivered
        sim.always("EveryMessageDelivered", ghost == set(msgs), lambda: f"missing {sorted(set(msgs) - ghost)}")
        return repr(sorted(vendor.accepted))
    return scenario


witness(
    "dst-ghost-from-environment", "dst",
    "A ghost fed by the SUT's own reading agrees with the SUT and hides a 'treat 429 as delivered' mutant; a ghost fed "
    "by what the vendor actually received catches it; the correct notifier passes.",
    ignored=lambda: catches(lambda: run_suite("g", {"throttled"}, _ghost(True, from_env=False)), "[EveryMessageDelivered]"),
    followed=lambda: catches(lambda: run_suite("g", {"throttled"}, _ghost(True, from_env=True)), "[EveryMessageDelivered]"),
    fixed=lambda: catches(lambda: run_suite("g", {"throttled"}, _ghost(False, from_env=True)), "[EveryMessageDelivered]"),
)


# --------------------------------------------------------------------------- replay check catches an unseeded draw
def _draw_scenario(unseeded: bool):
    def scenario(sim: SimulationContext) -> str:
        total = 0
        for i in range(10):
            v = random.randrange(1000) if unseeded else sim.next_int(1000)
            sim.record_step(f"draw {v}")
            total += v
        return str(total)
    return scenario


witness(
    "dst-replay-check", "dst",
    "A draw from the module-level RNG makes failures unreproducible but a sweep without replay stays green; the "
    "engine's replay check (on by default) flags it; draws through the SimulationContext pass.",
    ignored=lambda: catches(lambda: run_suite("r", set(), _draw_scenario(True), verify_seeds=0), "Non-determinism"),
    followed=lambda: catches(lambda: run_suite("r", set(), _draw_scenario(True)), "Non-determinism"),
    fixed=lambda: catches(lambda: run_suite("r", set(), _draw_scenario(False)), "Non-determinism"),
)


# --------------------------------------------------------------------------- known-bug pin flips when the bug is fixed
def _pin(bug_present: bool, use_helper: bool) -> tuple[bool, str]:
    """caught = once the bug is fixed, the suite goes red AND says it was a pinned known bug to flip into a guard."""
    observed = "stale" if bug_present else "fresh"

    def asserts_current_wrong():
        assert observed == "stale", f"expected 'stale', got {observed!r}"
    try:
        if use_helper:
            known_bug("#12", "a read after update returns the update", asserts_current_wrong)
        else:
            asserts_current_wrong()
    except AssertionError as e:
        msg = str(e).splitlines()[0]
        return ("known bug #12 no longer reproduces" in msg and "regression guard" in msg, msg)
    return (False, "green")


witness(
    "dst-known-bug-pin", "dst",
    "When a bug is fixed, a plain test asserting the old wrong behaviour goes red with a bare diff that reads like a new "
    "regression; `known_bug(...)` goes red saying the pinned bug no longer reproduces and must become a regression guard; "
    "while the bug is present the pin stays green.",
    ignored=lambda: _pin(bug_present=False, use_helper=False),
    followed=lambda: _pin(bug_present=False, use_helper=True),
    fixed=lambda: _pin(bug_present=True, use_helper=True),
)
