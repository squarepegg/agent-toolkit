"""Deterministic simulation engine: a Python sketch of the Kotlin template (templates/kotlin/), same semantics.

Not a library. Copy it next to your tests (pytest assumed) and keep it in step with the Kotlin template by hand.

    from dst import run_suite

    def scenario(sim):
        store, cache = FakeStore(), MemoCache(clock=sim.clock.ticker_nanos)   # real class, simulated dependency
        for i in range(60):
            op = sim.choose(["get", "put", "advance"])
            sim.record_step(f"op[{i}] {op}")
            ...
            sim.always("NoStaleRead", got == truth.get(k), lambda: f"got={got} truth={truth.get(k)}")
            sim.sometimes("cache_hit", hit)
        return repr(sorted(truth.items()))          # canonical end-state digest

    def test_seed_sweep():
        run_suite("MemoCache", {"cache_hit"}, scenario)

Repro: SIM_SEED=<seed> pytest -k test_seed_sweep.  Sweep: SIM_SEED_START=1001 SIM_SEEDS=1000 pytest ...
"""
from __future__ import annotations

import os
import random
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Sequence, TypeVar

T = TypeVar("T")
NANOS_PER_MILLI = 1_000_000
_FNV_OFFSET, _FNV_PRIME, _MASK = 0xCBF29CE484222325, 0x100000001B3, (1 << 64) - 1


class VirtualClock:
    """Monotonic ticker plus a skewable observation clock (see VirtualClock.kt)."""

    def __init__(self, initial_nanos: int = 1_000_000_000_000):
        self.monotonic_nanos, self.skew_nanos = initial_nanos, 0

    def ticker_nanos(self) -> int:
        return self.monotonic_nanos

    def observation_nanos(self) -> int:
        return self.monotonic_nanos + self.skew_nanos

    def now_millis(self) -> int:
        return self.observation_nanos() // NANOS_PER_MILLI

    def advance_nanos(self, delta: int) -> None:
        assert delta >= 0, "time only moves forward on the ticker"
        self.monotonic_nanos += delta

    def advance_millis(self, delta: int) -> None:
        self.advance_nanos(delta * NANOS_PER_MILLI)

    def step_back_observation_nanos(self, delta: int) -> None:
        """More adversarial than time.monotonic(): every finding that needs this needs a prod-reachability note."""
        assert delta > 0 and self.observation_nanos() - delta >= 0
        self.skew_nanos -= delta


class SimulationContext:
    def __init__(self, seed: int, declared_sometimes: Iterable[str] = (), trace_capacity: int = 200):
        self.seed, self.clock = seed, VirtualClock()
        self.declared = frozenset(declared_sometimes)
        self.reached: set[str] = set()
        self._rng = random.Random(seed)  # never use the module-level random, time.time() or uuid4() in a scenario
        self._trace: deque[str] = deque(maxlen=trace_capacity)
        self.transcript_hash, self.step_count = (_FNV_OFFSET ^ seed) & _MASK, 0

    def _mix(self, value: object) -> None:
        h = self.transcript_hash
        for b in repr(value).encode():
            h = ((h ^ b) * _FNV_PRIME) & _MASK
        self.transcript_hash = h

    def record_step(self, description: str) -> None:
        self.step_count += 1
        self._trace.append(f"#{self.step_count} @{self.clock.now_millis()}ms: {description}")
        self._mix((self.clock.observation_nanos(), description))

    def failure_report(self, headline: str, detail: str = "") -> str:
        lines = [f"{headline} on seed={self.seed} at step={self.step_count}",
                 f"Virtual time: {self.clock.now_millis()}ms", f"Reproduce single seed with env: SIM_SEED={self.seed}"]
        if detail:
            lines.append(f"Detail: {detail}")
        return "\n".join(lines + [f"Recent trace ({len(self._trace)} steps):", *("  " + t for t in self._trace)])

    def always(self, id: str, condition: bool, detail: Callable[[], str] = lambda: "") -> None:
        """Passes if never evaluated (Antithesis' alwaysOrUnreachable): pair conditional ones with a sometimes."""
        self._mix(("always", id, condition))
        if not condition:
            raise AssertionError(self.failure_report(f"Simulation invariant [{id}] violated", detail()))

    def sometimes(self, id: str, condition: bool) -> bool:
        if id not in self.declared:
            raise ValueError(f"Undeclared sometimes ID {id!r}; declare it up front: {sorted(self.declared)}")
        self._mix(("sometimes", id, condition))
        if condition:
            self.reached.add(id)
        return condition

    def next_int(self, until: int) -> int:
        v = self._rng.randrange(until)
        self._mix(v)
        return v

    def chance(self, percent: int) -> bool:
        return self.next_int(100) < percent

    def choose(self, items: Sequence[T]) -> T:
        return items[self.next_int(len(items))]


@dataclass
class SuiteSummary:
    seeds: list[int]
    single_seed: bool
    reached: set[str] = field(default_factory=set)


def _seeds(default_count: int, corpus: Sequence[int]) -> tuple[list[int], bool]:
    if os.environ.get("SIM_SEED", "").strip():
        return [int(os.environ["SIM_SEED"])], True
    count = int(os.environ.get("SIM_SEEDS") or default_count)
    start = int(os.environ.get("SIM_SEED_START") or 1)
    assert 1 <= count <= 10_000, f"seed count must be 1..10000, got {count}"
    return list(dict.fromkeys([*corpus, *range(start, start + count)])), False


def run_suite(name: str, declared_sometimes: set[str], scenario: Callable[[SimulationContext], str],
              default_seed_count: int = 150, verify_seeds: int = 25, corpus: Sequence[int] = ()) -> SuiteSummary:
    seeds, single = _seeds(default_seed_count, corpus)
    reached: set[str] = set()
    for i, seed in enumerate(seeds):
        ctx = SimulationContext(seed, declared_sometimes)
        try:
            digest = scenario(ctx)
        except AssertionError as e:
            if f"on seed={seed} " in str(e):
                raise
            raise AssertionError(ctx.failure_report(f"Suite [{name}] assertion failed: {e}")) from e
        reached |= ctx.reached
        if i < verify_seeds:
            again = SimulationContext(seed, declared_sometimes)
            if scenario(again) != digest or again.transcript_hash != ctx.transcript_hash:
                raise AssertionError(f"Non-determinism detected in suite [{name}] on seed={seed}")
    if not single and declared_sometimes - reached:
        raise AssertionError(f"Vacuous simulation in suite [{name}]: never reached {sorted(declared_sometimes - reached)}")
    return SuiteSummary(seeds, single, reached)


def known_bug(issue: str, correct_behaviour: str, reproduces: Callable[[], None]) -> None:
    """Pins current WRONG behaviour; fails loudly once the bug stops reproducing (see SimulationTestModes.kt)."""
    assert issue.startswith("#") or issue.startswith("http"), "known_bug needs a tracker reference"
    try:
        reproduces()
    except AssertionError as e:
        raise AssertionError(f"known bug {issue} no longer reproduces: flip this test into a regression guard "
                             f"asserting: {correct_behaviour}. First assertion that no longer holds: {e}") from e
