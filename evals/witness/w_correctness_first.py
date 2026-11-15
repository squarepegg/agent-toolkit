"""Witnesses for skills/correctness-first: the bank test, concurrent checkers, long runs of one op, faults.
Concurrency is explicit (seed-chosen interleaving of op phases, as the dst skill prescribes), so every run replays."""
from __future__ import annotations

from core import catches, witness
from dst import SimulationContext, run_suite

N_ACCOUNTS, START = 4, 100


# --------------------------------------------------------------------------- conservation (bank test) vs per-op checks
class SplitBank:
    """transfer = plan (read balances, check funds) then commit (write the planned absolute balances). Two transfers
    interleaved between plan and commit lose an update. atomic=True runs plan+commit as one step (by construction)."""

    def __init__(self, atomic: bool):
        self.bal, self.atomic = [START] * N_ACCOUNTS, atomic

    def plan(self, a, b, amt):
        if self.bal[a] < amt:
            return None
        return (a, self.bal[a] - amt, b, self.bal[b] + amt)

    def commit(self, p):
        a, va, b, vb = p
        self.bal[a], self.bal[b] = va, vb


def _bank(atomic: bool, conservation: bool):
    def scenario(sim: SimulationContext) -> str:
        bank, inflight = SplitBank(atomic), []
        for i in range(30):
            if inflight and sim.chance(50):
                p = inflight.pop(sim.next_int(len(inflight)))
                bank.commit(p)
                sim.record_step(f"op[{i}] commit {p}")
            else:
                a, b = sim.next_int(N_ACCOUNTS), sim.next_int(N_ACCOUNTS)
                if a == b:
                    continue
                amt = sim.next_int(60) + 1
                p = bank.plan(a, b, amt)
                sim.record_step(f"op[{i}] transfer {a}->{b} {amt}: {'ok' if p else 'insufficient'}")
                if p and bank.atomic:
                    bank.commit(p)
                elif p:
                    inflight.append(p)
                    sim.sometimes("two_in_flight", len(inflight) >= 2)
            # per-op checks: every balance stays non-negative
            sim.always("NonNegative", all(x >= 0 for x in bank.bal), lambda: f"{bank.bal}")
            if conservation:
                sim.always("TotalConserved", sum(bank.bal) == START * N_ACCOUNTS or bool(inflight),
                           lambda: f"total {sum(bank.bal)}")
        for p in inflight:
            bank.commit(p)
        if conservation:
            sim.always("TotalConserved", sum(bank.bal) == START * N_ACCOUNTS, lambda: f"total {sum(bank.bal)} != {START * N_ACCOUNTS}")
        return repr(bank.bal)
    return scenario


witness(
    "cf-bank-conservation", "correctness-first",
    "Interleaved read-then-write transfers lose updates (money is created) while every per-op check (funds checked, "
    "balances non-negative) passes; the bank-test conservation invariant catches it; an atomic transfer passes.",
    ignored=lambda: catches(lambda: run_suite("bank", {"two_in_flight"}, _bank(False, conservation=False)), "[TotalConserved]"),
    followed=lambda: catches(lambda: run_suite("bank", {"two_in_flight"}, _bank(False, conservation=True)), "[TotalConserved]"),
    fixed=lambda: catches(lambda: run_suite("bank", set(), _bank(True, conservation=True)), "[TotalConserved]"),
)


# --------------------------------------------------------------------------- a concurrent checker sees what a final check cannot
def _debit_credit(two_step: bool, concurrent_reader: bool):
    """transfer = debit, then (later) credit. Nothing is ever lost, so the quiescent end state is always right, but a
    reader in between sees money missing (an isolation bug). two_step=False applies both in one step."""
    def scenario(sim: SimulationContext) -> str:
        bal, pending = [START] * N_ACCOUNTS, []
        for i in range(30):
            r = sim.next_int(3)
            if r == 0 and pending:
                b, amt = pending.pop(0)
                bal[b] += amt
                sim.record_step(f"op[{i}] credit {b} {amt}")
            elif r == 1 and concurrent_reader:
                seen = sum(bal)
                sim.record_step(f"op[{i}] reader sums {seen}")
                sim.sometimes("read_during_transfer", bool(pending) or not two_step)
                sim.always("ReaderSeesTotal", seen == START * N_ACCOUNTS, lambda: f"reader saw {seen}")
            else:
                a, b = sim.next_int(N_ACCOUNTS), sim.next_int(N_ACCOUNTS)
                amt = sim.next_int(20) + 1
                if a != b and bal[a] >= amt:
                    bal[a] -= amt
                    if two_step:
                        pending.append((b, amt))
                    else:
                        bal[b] += amt
                    sim.record_step(f"op[{i}] debit {a} {amt} for {b}")
        for b, amt in pending:          # quiesce
            bal[b] += amt
        sim.always("FinalTotal", sum(bal) == START * N_ACCOUNTS, lambda: f"final {sum(bal)}")
        return repr(bal)
    return scenario


witness(
    "cf-concurrent-checker", "correctness-first",
    "A transfer that debits, then credits later never loses money, so a check once the system is quiescent always "
    "passes; a reader running concurrently with the ops sees money missing; a one-step transfer passes both.",
    ignored=lambda: catches(lambda: run_suite("dc", set(), _debit_credit(True, concurrent_reader=False)), "[ReaderSeesTotal]"),
    followed=lambda: catches(lambda: run_suite("dc", {"read_during_transfer"}, _debit_credit(True, concurrent_reader=True)), "[ReaderSeesTotal]"),
    fixed=lambda: catches(lambda: run_suite("dc", {"read_during_transfer"}, _debit_credit(False, concurrent_reader=True)), "[ReaderSeesTotal]"),
)


# --------------------------------------------------------------------------- long runs of one op vs a uniform "drunken walk"
class WriteQueueKV:
    """Writes go to a queue that a background flusher drains whenever any non-write op runs. Above CAP queued writes
    the overflow path kicks in; overflow_bug drops the oldest queued write instead of flushing synchronously."""
    CAP = 48

    def __init__(self, overflow_bug: bool):
        self.store, self.q, self.bug = {}, [], overflow_bug

    def _flush(self):
        for k, v in self.q:
            self.store[k] = v
        self.q.clear()

    def put(self, k, v):
        self.q.append((k, v))
        if len(self.q) > self.CAP:
            if self.bug:
                self.q.pop(0)
            else:
                self._flush()

    def get(self, k):
        self._flush()
        return self.store.get(k)

    def delete(self, k):
        self._flush()
        self.store.pop(k, None)


def _kv(bug: bool, long_runs: bool):
    def scenario(sim: SimulationContext) -> str:
        kv, truth, i, run_len = WriteQueueKV(bug), {}, 0, 0
        while i < 200:
            op = sim.choose(["put", "get", "delete"])
            reps = sim.next_int(120) + 1 if long_runs and sim.chance(10) else 1
            for _ in range(reps):
                k = f"k{sim.next_int(16)}"
                if op == "put":
                    kv.put(k, i)
                    truth[k] = i
                    run_len += 1
                    if "queue_overflow" in sim.declared:
                        sim.sometimes("queue_overflow", run_len > WriteQueueKV.CAP)
                else:
                    run_len = 0
                    if op == "get":
                        got = kv.get(k)
                        sim.always("ReadsLatestWrite", got == truth.get(k), lambda: f"{k}: got {got} want {truth.get(k)}")
                    else:
                        kv.delete(k)
                        truth.pop(k, None)
                i += 1
            sim.record_step(f"{op} x{reps}")
        for k in sorted(truth):
            got = kv.get(k)
            sim.always("ReadsLatestWrite", got == truth[k], lambda: f"{k}: got {got} want {truth[k]}")
        return repr(sorted(truth.items()))
    return scenario


witness(
    "cf-long-runs", "correctness-first",
    "An overflow path that only 49+ consecutive writes reach (it drops a write) is never reached by uniformly random "
    "ops in 3,000 seeds (the 'drunken walk'); a generator that emits long runs of one op reaches and catches it in the "
    "PR tier; with the overflow fixed it passes.",
    ignored=lambda: catches(lambda: run_suite("kv", set(), _kv(True, long_runs=False), default_seed_count=3000), "[ReadsLatestWrite]"),
    extra_caught_uniform_never_overflows=lambda: catches(lambda: run_suite("kv", {"queue_overflow"}, _kv(False, long_runs=False),
                                                                            default_seed_count=3000), "Vacuous simulation"),
    followed=lambda: catches(lambda: run_suite("kv", {"queue_overflow"}, _kv(True, long_runs=True)), "[ReadsLatestWrite]"),
    fixed=lambda: catches(lambda: run_suite("kv", {"queue_overflow"}, _kv(False, long_runs=True)), "[ReadsLatestWrite]"),
)


# --------------------------------------------------------------------------- bugs show up under faults; idempotency keys rule them out by construction
def _pipeline(faults: bool, idempotency_key: bool):
    """Client sends events; on timeout it retries. Fault 'timeout then late success': the server stored the event but
    the client saw a timeout. Invariant per id: sent once = stored once (in = stored + rejected)."""
    def scenario(sim: SimulationContext) -> str:
        stored: dict[str, int] = {}
        seen_keys: set[str] = set()
        ids = [f"e{j}" for j in range(sim.next_int(10) + 5)]
        for eid in ids:
            for attempt in range(3):
                late_success = faults and sim.chance(25)
                if not (idempotency_key and eid in seen_keys):
                    stored[eid] = stored.get(eid, 0) + 1
                    seen_keys.add(eid)
                if late_success:
                    sim.sometimes("late_success", True)
                    sim.record_step(f"{eid} attempt {attempt}: stored, client saw timeout")
                    continue
                sim.record_step(f"{eid} attempt {attempt}: ack")
                break
        for eid in ids:
            sim.always("StoredOnce", stored.get(eid, 0) == 1, lambda: f"{eid} stored {stored.get(eid, 0)}x")
        return repr(sorted(stored.items()))
    return scenario


witness(
    "cf-faults", "correctness-first",
    "A retrying client double-stores events only under a 'timeout then late success' fault: the fault-free workload "
    "passes; the same workload with faults catches it; an idempotency key (ruled out by construction) passes with faults.",
    ignored=lambda: catches(lambda: run_suite("p", set(), _pipeline(False, False)), "[StoredOnce]"),
    followed=lambda: catches(lambda: run_suite("p", {"late_success"}, _pipeline(True, False)), "[StoredOnce]"),
    fixed=lambda: catches(lambda: run_suite("p", {"late_success"}, _pipeline(True, True)), "[StoredOnce]"),
)
