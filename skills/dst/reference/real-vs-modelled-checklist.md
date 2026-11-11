# Checklist: real vs modelled, per component

Fill one row per component that the system under test (SUT) touches, and put the table in the suite's KDoc or
README. A row you can't fill in is a decision you haven't made yet.

| Component | Real or modelled? | Why | What the model abstracts away | Conformance check | Physical limits (modelled?) |
|---|---|---|---|---|---|

## Decide per component

Go through these in order; the first "yes" decides.

1. **Is it pure, or deterministic given injected time and RNG?** (parsers, merge rules, predicates, hashes, in-memory
   caches with an injectable ticker/clock/executor/scheduler.) Then use the **real** class. Inject `VirtualClock` and
   run its executor inline (`Runnable::run`). Check for hidden randomness: Caffeine's size-eviction admission uses
   `ThreadLocalRandom`, so either keep the cache below its size limit, or declare the suite non-replayable.
2. **Can a test seam make it deterministic without changing behaviour?** (a constructor parameter for clock,
   executor or ID source.) Add the seam, then use the **real** class. Seams must default to production behaviour.
3. **Is it a framework or runtime you can't run in-process** (Restate's `ObjectContext`, a DB, a vendor HTTP API, Kafka)?
   Then **model** it, minimally: model only the semantics the SUT relies on. Write each one down, and add a
   conformance check (step 5 of SKILL.md). Don't mock the runtime's API call by call. Model the *state machine* it
   implements (the ordering, LWW rules, tombstones, retries), then check that model against the real runtime.
4. **Is it a component of your own that a modelled framework hosts** (a VO handler, a Kafka consumer)? Prefer
   extracting its decision logic into a pure function and calling that real function from the model. One engine does this
   with a `becomesCurrent` predicate and a `nextStamp` function that the model calls directly. Hand-copying the handler body into the
   model is the drift risk; minimise it, and cover what remains with conformance.

## For every modelled component, answer these

- [ ] Which real calls, tables and handlers does it replace? List them by name.
- [ ] Ordering: what does the real component serialize (per key, per partition, global), and what may interleave?
- [ ] Conflict rules: LWW on which timestamp? What wins a tie? Do tombstones beat equal timestamps? Is it a
  partition-level or a cell-level delete?
- [ ] Failure modes: which calls can fail, partially apply, time out and then succeed late, or be retried (and by whom)?
- [ ] Durability and replay: what is journaled, and what is re-executed on retry? Is it idempotent?
- [ ] Clock: which clock does production read (`System.nanoTime` is monotonic; the wall clock can step), and at which
  resolution? Can two events share a tick?
- [ ] **Physical limits**, each marked modelled / not modelled / not applicable, with the source of the number:

| Limit | Example | Modelled? |
|---|---|---|
| request / batch / row / value size | a database's maximum batch size (for example Scylla's `batch_size_fail_threshold_in_kb`, 1,024 KiB) | |
| future-timestamp guard | a database's future-timestamp restriction (for example Scylla's `restrict_future_timestamp`) | |
| timeouts (client, server, journal step) | a CLI or client request timeout | |
| quotas and rate limits | vendor API 429s | |
| fan-out / collection cardinality | a cap on group size | |
| retention / TTL / GC | a durable-execution journal's retention window | |
| runtime behaviour on a non-terminal error | a durable runtime that retries forever, so a paused invocation blocks its key | |
| clock resolution and monotonicity | `System::nanoTime` (monotonic) vs `stepBackObservationClockNanos` | |
| key length, encoding, case | a cap on cached-key length; hex case normalisation | |

A limit that isn't modelled is invisible to the sim. Either model it (a size check in the simulated dependency, plus an
op that reaches it, plus a `sometimes` that proves the op reached it), or make sure a real-infra test covers it, and
say which.

## Red flags

- The simulated dependency has no conformance test. (This was one engine's pipeline sim.)
- A KDoc says the model is "faithful to" or "exact", and no test checks it.
- The model only gets corrected when a reviewer happens to notice. Reviewers caught three of one engine's model
  infidelities in review; no check did.
- A counterexample uses an operation production can't perform (a review caught one).
- The suite only works with a large cache "so eviction never happens". Then note that eviction stays untested
  everywhere except the non-replayable suite.
