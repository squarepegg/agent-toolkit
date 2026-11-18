"""Advice witnesses: free, deterministic proof that a skill's advice is correct (stdlib only, no model, no network).

A witness turns one claim from a SKILL.md into an executable experiment with a planted bug and a fixed twin:

  ignored*   the advice NOT followed, on the buggy code   -> must MISS the bug   (the advice is needed)
  followed   the advice followed, on the buggy code       -> must CATCH the bug  (the advice works)
  fixed      the advice followed, on the fixed code       -> must PASS           (no false alarm, not vacuous)

All three must hold, or the claim is not witnessed. A witness may add `extra:<name>` arms with an explicit expectation
(e.g. "the vacuity gate fires"). Every arm is a function returning (caught: bool, detail: str); seeds are fixed, so a
witness gives the same result on every machine.
"""
from __future__ import annotations

import dataclasses
import os
import sys
import time
from typing import Callable

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "skills", "dst", "templates", "python"))   # witnesses use the skill's own engine


@dataclasses.dataclass
class Arm:
    name: str
    expect_caught: bool
    fn: Callable[[], tuple[bool, str]]


@dataclasses.dataclass
class Witness:
    id: str
    skill: str
    shows: str
    arms: list[Arm]


@dataclasses.dataclass
class ArmResult:
    name: str
    expect_caught: bool
    caught: bool | None
    detail: str
    seconds: float

    @property
    def ok(self) -> bool:
        return self.caught is self.expect_caught


REGISTRY: dict[str, Witness] = {}


def witness(id: str, skill: str, shows: str, **arms: Callable[[], tuple[bool, str]]) -> Witness:
    """Register a witness. Keyword names: ignored / ignored_<x> (expect miss), followed (expect catch), fixed (expect
    pass), extra_caught_<x> / extra_missed_<x> (explicit expectation)."""
    if id in REGISTRY:
        raise ValueError(f"duplicate witness id {id!r}")
    out = []
    for name, fn in arms.items():
        if name == "followed" or name.startswith("extra_caught_"):
            out.append(Arm(name, True, fn))
        elif name == "fixed" or name.startswith("ignored") or name.startswith("extra_missed_"):
            out.append(Arm(name, False, fn))
        else:
            raise ValueError(f"{id}: unknown arm name {name!r}")
    names = {a.name for a in out}
    if "followed" not in names or "fixed" not in names or not any(n.startswith("ignored") for n in names):
        raise ValueError(f"{id}: needs an ignored*, a followed and a fixed arm; has {sorted(names)}")
    w = Witness(id, skill, shows, out)
    REGISTRY[id] = w
    return w


def run_arm(arm: Arm) -> ArmResult:
    saved = {k: os.environ.pop(k) for k in list(os.environ) if k.startswith("SIM_")}   # the engine reads SIM_*
    t0 = time.time()
    try:
        caught, detail = arm.fn()
        if not isinstance(caught, bool):
            raise TypeError(f"arm returned {type(caught).__name__}, not bool")
    except Exception as e:   # a crashing arm is neither a catch nor a miss
        caught, detail = None, f"CRASH {type(e).__name__}: {e}"
    finally:
        os.environ.update(saved)
    return ArmResult(arm.name, arm.expect_caught, caught, " ".join(str(detail).split())[:240], time.time() - t0)


def run_witness(w: Witness) -> list[ArmResult]:
    return [run_arm(a) for a in w.arms]


def catches(fn: Callable[[], object], marker: str) -> tuple[bool, str]:
    """Run fn; caught = it raised an AssertionError whose message contains marker. Any other AssertionError is a CRASH
    (a different failure than the one the witness is about must not count as a catch)."""
    try:
        fn()
    except AssertionError as e:
        msg = str(e)
        if marker in msg:
            return True, msg.splitlines()[0]
        raise RuntimeError(f"failed, but not with {marker!r}: {msg.splitlines()[0] if msg else msg!r}") from e
    return False, "no violation"


def load_all() -> dict[str, Witness]:
    import importlib
    for f in sorted(os.listdir(HERE)):
        if f.startswith("w_") and f.endswith(".py"):
            importlib.import_module(f[:-3])
    return REGISTRY
