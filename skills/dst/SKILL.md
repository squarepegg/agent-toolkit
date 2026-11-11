---
name: deterministic-simulation-testing
description: Use when adding or extending deterministic simulation tests (DST), Antithesis-style, for a concurrent, distributed, cached, retried or otherwise stateful component, in Kotlin/JVM, with sketches for other languages. Covers seeded op generators, a virtual clock, deterministic interleaving and fault injection, always/sometimes invariants, differential and reference oracles, seed sweeps with replay checks, known-bug pins, mutant catches and the required model-vs-real conformance check. Also use when asked to "simulate", "seed sweep", "Antithesis-style", "find races", or to reproduce or triage a failing SIM_SEED. Not for plain unit tests, single-function property tests, or TLA+/Lean modelling on its own.
---

# Deterministic simulation testing (DST)

DST runs the **real** component, in-process, in an environment you fully control: virtual time, a seeded RNG,
explicit interleavings and injected faults. It runs many seeds and checks invariants after every step. A failing seed
replays exactly.

Be honest about what it buys. In the first real use, 1 of 14 bug tests came
from the random sweep, and the TLA+ model already had that mechanism. The rest came from formal models, LLM reviewers
and mutants. Running 17–66× more seeds found nothing new. The sims paid off as **executable counterexample and
regression nets, and as mutant catchers**. Discovery has to be designed in (step 4), and the model has to be checked
against reality (step 8), or the suite becomes a self-consistent fiction.

Engine and skeletons: `templates/kotlin/` (see its README for JUnit 5, kotest and Gradle wiring). A Python sketch is
in `templates/python/dst.py`. For a long local sweep, use `scripts/seed-sweep.sh`. The `reference/` directory holds the
Antithesis vocabulary and the model-vs-real checklist.

**If the repo already has an engine**, use it in a test-only task; don't fork shared fixtures. Some features here are
**template v1 only**: `SIM_SEED_START`, `corpusSeeds`, `requiredSometimesIds`, `greedyCoverSeeds`, the `rarest
sometimes` line, seed-tagged wrapping of non-`always` failures, and `SimulationEngineSelfTest`. Port the ones you need
in a separate small PR, or note in the report which ones were unavailable. An engine already in the repo may predate
v1 and have none of them.

## Hard rules

1. **Real production classes.** Model only what can't run deterministically in-process, and record every abstraction
   (step 2).
2. **A hand-written model needs a conformance test** against the real thing (step 8). If it has none, say so in the
   suite KDoc and the final report, as an open risk. **A model that copies production logic protects the copy, not
   production.** In the durable-execution dry run, a deliberate break in the real handler stayed green in the smoke sweep, and
   only conformance caught it. Say this in the report, and add the source tripwire from step 2.
3. **Never leave broken behaviour asserted by a green, innocent-looking test.** A test that asserts current wrong
   behaviour is `known bug #<issue>` and goes through `knownBug(...)` (step 6).
4. **Every property is mutation-checked** before you claim it (step 9).
5. **Every finding states whether production can reach it** (step 11). The sim's environment can be more adversarial
   than prod's.
6. **The PR tier stays cheap:** about 150 seeds, no Docker, seconds per suite (step 10).

## Checklist

```
- [ ] 1  Decide it pays off (else: property test / TLA+ / real-infra test)
- [ ] 2  Real-vs-modelled table + physical-limits table written
- [ ] 3  Determinism: virtual clock, single RNG, explicit interleaving, faults as ops
- [ ] 4  Op generator built for discovery (rare states targeted by declared `sometimes`)
- [ ] 5  Properties: always (named after formal invariants) / sometimes / oracle / quiescent end state
- [ ] 6  Mode-named tests: regression guard / known bug #N / mutant catch / accepted / seed sweep
- [ ] 7  Determinism verified (replay check on), failure output has seed + trace
- [ ] 8  Conformance test of every model against real infrastructure (coverage-chosen seeds, per-op timeouts)
- [ ] 9  Every property mutation-checked (break → see it fail → restore)
- [ ] 10 CI tiers wired (Gradle forwards SIM_* and makes them task inputs)
- [ ] 11 Findings reported with repro, invariant, prod reachability, severity
```

## 1. When it pays off

Use DST when correctness depends on **ordering, timing, retries, caching or partial failure** across calls, and the
logic lives in code you can call directly. Examples: caches with invalidation, write fan-outs, idempotent retries,
LWW/tombstone rules, leases, per-key queues, sync protocols.

Choose something else when:

| Situation | Use instead |
|---|---|
| one pure function, no state across calls | a property test (kotest-property, Hypothesis) |
| you are still designing the protocol, and the code doesn't exist yet | TLA+ (exhaustive within bounds), then DST to replay its traces against the code |
| an algebraic law over all inputs | Lean / a proof, plus a conformance test |
| the risk is in the infrastructure (sizes, timeouts, config, DB semantics) | a real-infra test (Testcontainers, `@RestateTest`); a model can't see it |
| the whole system, unmodified, under network and node faults | Antithesis itself, or Jepsen-style real clusters |
| a durable-execution handler (Restate, Temporal) whose logic sits inline in runtime calls | **both**: DST over a model of the runtime for the decision logic under kills and retries, **plus** conformance and real-infra tests for the runtime semantics. Neither one alone is enough |

If a TLA+/Lean model exists, DST is how you connect it to the code. Name the invariants the same, and turn each
counterexample into a deterministic test.

## 2. Choose the system under test; decide real vs modelled per component

Work through `reference/real-vs-modelled-checklist.md`. In short:

- **Real:** pure logic, and anything deterministic once time, executor and RNG are injected (add constructor seams if
  needed, defaulting to production behaviour). Find hidden randomness in dependencies. For example, Caffeine's
  size-eviction admission uses `ThreadLocalRandom`.
- **Modelled:** runtimes you can't run in-process, such as a workflow engine's context, a database or a vendor API.
  Model the *state machine* the SUT relies on (ordering, conflict rules, tombstones, failures), not individual mocked
  calls. Pull the SUT's decision logic out into pure functions, and have the model call those real functions.
- **Logic you can't extract** in a test-only task, for example a handler written inline against `ObjectContext` in a
  repo that forbids mocking it: copying the handler into the model is acceptable, but then:
  - name the copy a *model* everywhere;
  - call every real pure class it uses (contract constructors, codecs, policies);
  - add a **source tripwire**: a smoke test that hashes the production file(s) the copy mirrors and fails with
    "production changed: re-check the model, then update the hash";
  - run conformance on every change to those files, and at least nightly;
  - propose the extraction refactor as its own PR.
- **Write the two tables** in the suite KDoc or README:
  - real/modelled per component, with the abstractions;
  - **physical limits**: sizes, timeouts, quotas, future-timestamp guards, cardinalities, retention, clock source and
    resolution. Mark each one modelled, not modelled, or not applicable.

  A limit you don't model is invisible. One engine's pipeline model had no sizes, and production hit the database's
  `Batch too large` error with a batch just over its limit.

## 3. Make the environment deterministic

- **Time.** Use `VirtualClock`, and inject it everywhere:
  - `tickerNanos()` is monotonic, for TTLs, Caffeine `Ticker` and timeouts;
  - `observationNanos()` / `nowMillis()` are what the app reads as "the time";
  - `stepBackObservationClockNanos` tests skew. Use it only when production's clock can step; `System.nanoTime`
    can't.

  Advance time explicitly as an op.
- **Randomness.** Draw every choice through the one `SimulationContext` (`nextInt`, `choose`, `chance`, `shuffled`).
  Never use `Random.Default`, `UUID.randomUUID()` or the wall clock. Derive IDs from the seed and the op index.
- **Interleaving.** Give the SUT no threads. Use inline executors and disabled schedulers, then create concurrency
  explicitly:
  - **phase splitting:** expose `plan`/`read` and `apply`/`write` as separate steps, and interleave other ops between
    them (for example a `readAndPlan` step and an `executePlannedWrites` step);
  - **re-entrant hooks inside dependency calls:** a hook that runs *during* `delegate.read()` lets another op land
    mid-call (the skeleton's `FaultInjectingStore.onInsideRead`; one engine's `onInsideLookupHook`);
  - **queues** with an explicit, seed-chosen drain order (FIFO, LIFO, or `shuffled`).
- **Faults are ops.** Put each one in the generator with a seed-chosen rate: failure before a write, partial write,
  timeout then late success, retry or replay, kill between journal steps, duplicate delivery, clock step, eviction.
  Wrap the dependency (real or model) in one fault wrapper, so the sim and the conformance test share the schedule.
- **Aim faults at labelled points**, not ordinals. "Kill at the k-th await" silently retargets when production adds an
  await. Name the points ("kill after `run(\"send-positive\")`") and declare a `sometimes` per label, so a point that
  disappears trips the vacuity gate instead of quietly changing the test.
- **Durable-execution runtimes** (Restate, Temporal, workflow engines): before modelling kills and replays, write down
  from the docs and the code:
  - what is durable at each point: journaled step results, and state writes, which may stay pending until a
    suspension point;
  - what a replay re-executes;
  - what a kill drops;
  - what the runtime does with a non-terminal error (Restate retries it and can block the key).

  These semantics are model-only unless the real stack can be killed deterministically, so list them under "not
  covered by conformance".

## 4. Build the generator for discovery

Seed count is not the lever (the reference engine: 150 → 2,500 / 10,000 seeds, nothing new). These are:

- **Richer ops and pools.** Cover every public operation, plus admin and delete paths. Use small key pools, so ops
  collide, and timestamp pools with **ties**. Use unique values, so a stale answer is detectable.
- **An unrestricted mode, next to any "safe envelope".** An envelope that skips known-bug preconditions (one engine
  skipped hard deletes after soft deletes, which was a known bug's precondition) is green by construction around the bug and
  blind to its neighbours. Run the unrestricted generator too. Where a known bug fires, pin it (step 6) and filter
  that *specific* violation out, rather than the whole state region.
- **`sometimes` for rare states, declared up front.** Examples: two in-flight ops on one key, a tie at the same
  millisecond, a delete during a read, a retry after a partial write, a fault at the last step, an exact TTL
  boundary. Read the runner's `rarest sometimes` line and tune the weights until rare states are reached in a healthy
  share of seeds.
- **Longer runs** go in the nightly tier (step 10), not the PR tier: more ops per seed and more seeds, chunked with
  `scripts/seed-sweep.sh`.
- **A seed corpus.** Keep every seed that once failed or that alone reaches a rare `sometimes`, and run it on every
  PR (`corpusSeeds`). This is a cheap stand-in for coverage-guided seeds.
- **Formal traces.** Replay every TLA+ counterexample and mutant as a deterministic test. Use model-checker
  reachability probes as `sometimes` targets.

## 5. Properties

- **`always(id, cond)`** for safety invariants, checked after every op. Name them after the TLA+/Lean invariant when
  one exists (`MappingsClosed`, `NoOpPredicateSound`, after `skip_sound`). This engine's `always` passes if never
  evaluated. When it sits inside a condition, add a `sometimes` on that condition.
- **`sometimes(id, cond)`** for anti-vacuity: the suite fails if a declared ID is never reached across all seeds.
- **Feed reference/ghost state from the environment, never from the model.** Record what the dependency *actually*
  received and answered (in the fault wrapper or scripted vendor), not the SUT's or model's interpretation of it. In
  the durable-execution dry run, a ghost fed by the model's own reading hid a mutant completely.
- **Real contract classes as named properties.** Wrap a real constructor's `require` as
  `always("<Contract>", runCatching { ... }.isSuccess)`, so a violation is attributable. A bare
  `IllegalArgumentException` is not a property.
- **Formal "invariants" that are meant to fail**, i.e. claims the TLA+ model exists to refute (e.g. a
  `ReceiptsBounded` property on a delivery contract): map each one to a `sometimes` (the counterexample must be reachable) plus an
  `accepted - ...` scenario test with the minimal trace.
- **Oracles.** Prefer, in order:
  1. **differential:** shipped code against a simpler variant on the same ops. For example, guarded skip against
     always-write must reach identical states, or a memo against TTL 0 must return identical results.
  2. **reference model:** a tiny "truth" structure, such as the latest committed value per key.
  3. **hand-written expectations** only in deterministic scenario tests.
- **Quiescent end state.** Drain every queue, stop faults, then check convergence: replicas, caches and stores all
  agree. (This plays the role of Antithesis' `eventually_` / `finally_` commands.)

Glossary and Antithesis mapping: `reference/antithesis-glossary.md`.

## 6. Test modes and naming

One mode per test, and the name starts with it. Use the helpers in `templates/kotlin/SimulationTestModes.kt`.

| Mode | Name | Asserts | Goes red when |
|---|---|---|---|
| regression guard | `regression guard - <behaviour>` | the fixed behaviour | the bug returns |
| known bug | `known bug #<issue> - <what goes wrong>` + `@Tag("known-bug")` | the current wrong behaviour, inside `knownBug(issue, correctBehaviour) { ... }` | the bug is fixed. Then flip it to a regression guard and close the issue |
| mutant catch | `mutant catch - <mutant> - <what it breaks>` | `expectViolation("<InvariantId>") { sweep over the mutant }` | the property can no longer see the fault |
| accepted | `accepted - <trade-off>` | a documented trade-off, citing the KDoc or ADR | the trade-off changes |
| seed sweep | `seed sweep - <scope>, <N> seeds` | `runSuite(...)` | any seed violates an invariant, the replay differs, or a `sometimes` is never reached |

Don't use `bug catch`. In one engine that name covered unfixed bugs (1, 3, 8), fixed ones (9–14) and mutants (7),
so a green run hid three known-broken behaviours. When a known-bug test is fixed, it becomes a regression guard
(one engine's bug catch 5 was correctly renamed once it was fixed).

## 7. Determinism

- `runSuite` replays the first 25 seeds and compares their transcript hashes and state digests. The scenario must
  return a **canonical** digest: sorted, with no identity hash codes and no outside time.
- Turn the replay check off only with `nonReplayableReason` (e.g. a dependency's own unseedable RNG), and keep a
  replayable sibling suite.
- Reproduce with `SIM_SEED=<n>` or `-Dsim.seed=<n>`. Sweep with `SIM_SEEDS=<count>` and `SIM_SEED_START=<first>`.
  Failures print the seed, step, virtual time and the last 200 recorded steps. `recordStep` every op and every fault.
- The replay runs in the same JVM, so it catches leaked state and wall-clock use. It does not catch differences
  between JVMs or platforms. Avoid iteration over `HashSet`s of objects that have no stable `hashCode`.

## 8. Conformance: required for every hand-written model

Generate ops **up front** from the seed, as a `List<Op>` that doesn't depend on state. Interpret them in **one**
interpreter, and replay the same list against the real dependency (`templates/kotlin/SkeletonConformanceTest.kt`):

- **Choose the sample by coverage, not taste.** `greedyCoverSeeds(candidates, catalog, mustReach)` runs the model and
  picks seeds until every `sometimes`, and each op × answer class, is reached. Pin the result (aim for 5–10 seeds ×
  20–40 ops), add every seed that ever failed, and add a smoke test that the pinned sample still covers (the
  skeleton has one). A hand-picked `1, 2, 3, 7, 42` missed every offset rejection in the durable-execution dry run, so a
  model drift passed conformance.
- Run them on real infrastructure (Testcontainers, `@RestateTest` pinned to the repo's image), and tag the test as
  the repo tags Docker tests. A narrower conformance op mix keeps the full ID catalog as `declaredSometimesIds`, and
  passes the subset it can reach as `requiredSometimesIds`.
- **Real stacks fail differently:**
  - give **every real op a timeout**: a runtime that retries a failing call forever hangs the whole class until
    `@Timeout` (this happened in the dry run: 10 minutes);
  - **never assert inside callbacks the runtime runs** (journaled blocks, retried handlers): the runtime catches and
    retries, and the failure vanishes. Record it, then assert after the op.
- Compare normalized per-op results and the end state. On divergence, fail with the seed, the op index and a diff. A
  divergence is a finding: either the model is wrong, or reality does something the model never considered.
- Where the real stack serializes something the model interleaves, restrict conformance to orderings both can express,
  or gate the real stack deterministically. **List what stays model-only** in the suite README.
- Prove the conformance test detects drift: change the model one rule at a time **in ways only the diff can see**,
  such as an output label or a value no model invariant checks, and watch the diff fail. A drift that trips the
  model's own invariants first proves nothing about conformance. Also break the **production** code once, and show
  that conformance (not the smoke sweep) catches it.

## 9. Mutation-check every property

For each `always` and each oracle, break the SUT or the model in the way the property is meant to catch, run the
sweep, confirm the **intended** ID fails, then restore. For `sometimes`: show once per suite that removing an op trips
the vacuity gate, and individually check each `sometimes` that guards a conditional `always`. Record each break and
the ID that caught it in the PR. Keep the important ones as `mutant catch` tests with test-local faulty variants, not
production flags.

If a stronger check fires first (a step-level oracle, a real contract class), the intended one is **shadowed**.
Either record "shadowed by X", or rerun with X disabled to show the intended one also sees the fault. An invariant
that is always shadowed is redundant, unless it names a formal invariant. A break that **survives** is a coverage gap:
report it. For example, no property checked the backoff schedule.

Also break the harness: a draw from `Random.Default` must trip the replay check. The skeleton and engine were checked
this way (template README, `SimulationEngineSelfTest`).

## 10. CI wiring

| Tier | Seeds | Budget | Contents |
|---|---|---|---|
| PR (smoke, no Docker) | 150 per suite, plus `corpusSeeds` | about 5 s per suite; the whole sim tier in seconds (the reference engine: 4 classes, about 2.3 s) | sweeps, scenario tests, mutant catches, known-bug pins |
| nightly / manual | 2,000–10,000+, chunked | minutes | the same suites, run through `scripts/seed-sweep.sh` |
| real-infra (Docker) | 5–10 sampled seeds | under 10 min | conformance tests |

- The Gradle test JVM must receive `sim.*` properties, and `SIM_*` must be **task inputs**. Otherwise a repro run is
  UP-TO-DATE and silently does nothing. Copy the snippet in `templates/kotlin/README.md`.
- A class-level `@Timeout` caps long sweeps, so chunk them. Tests that expect the default seed set must pin the
  explicit seed parameters, or an exported `SIM_SEEDS` leaks into them.
- **A failing seed in CI:** copy the seed from the failure into the issue, reproduce it locally with `SIM_SEED`, add
  the seed to `corpusSeeds`, and either fix the bug or pin it as a known bug in the same PR.

## 11. Reporting findings

For each finding, give:
- a **minimal repro**: a deterministic scenario test of a few ops, plus the seed;
- the **invariant** violated, with its formal name;
- **prod reachability**: does production's environment allow it? Consider clock source, real concurrency, deployment
  config (is the feature even on?), and whether every op is an operation production can perform;
- **severity**: what breaks, for whom, and how often;
- **found by**: sweep, scenario, formal model, review or mutant. Be honest; it shapes where effort goes.

Two examples:
- A bug catch needed a backward observation-clock step that `System::nanoTime` can't produce, so it was latent and
  model-only for now.
- A repro first used an illegal direct table write (a review caught it); it was rewritten with legal calls only.

## Anti-patterns (from one engine and a durable-execution dry run)

- Saying the smoke sweep tests production when it runs a copy of the handler.
- A hand-picked conformance seed sample, and drift checks that the model's own invariants catch first.

- A hand-written model with "faithful to" in its KDoc, and no conformance test.
- `bug catch` tests that pass *because* the bug exists.
- A safe envelope as the only sweep.
- More seeds instead of better generators.
- Modelling a runtime with no physical limits, then trusting it about payload sizes or timestamps
  (`Batch too large`, `restrict_future_timestamp`).
- Counterexamples built from operations production can't perform.
- Calling a sim-only finding a prod bug without a reachability note.
- Repro instructions that Gradle silently doesn't honour.
- `always` inside a branch that is never taken, with no `sometimes` on the branch.

## Final report

Copy this into the PR or the handoff:

```
Engine: repo's | template v1 (unavailable features: ...)
SUT (real classes): ...           Modelled: ... (conformance: <test> | NONE — open risk; copied logic: <files>, tripwire: <test>)
Physical limits modelled: ...     Not modelled: ...
Invariants (always): ...          Sometimes: N declared, all reached; rarest: ...
Oracles: differential <a vs b> / reference model / quiescent
Mutation checks: <break> → <id caught>, ...
Findings: <id> repro=<test>/<seed> invariant=<name> prod-reachable=<yes/no/why> severity=<...> found-by=<...>
Known-bug pins: #..   Cost: PR tier <s>, nightly <seeds>/<min>
Not covered: ...
```
