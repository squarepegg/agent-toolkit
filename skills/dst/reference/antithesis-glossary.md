# Glossary: this skill's engine and Antithesis

The engine is "Antithesis-style": it borrows Antithesis' assertion vocabulary and the idea of deterministic
replay. It does **not** use Antithesis. Antithesis runs whole containers, unmodified, inside a deterministic
hypervisor and searches the state space with guided exploration. This engine runs one JVM test, in-process, over
seeds 1..N. Terms were checked against the Antithesis docs on 2026-11-04 (links below).

| Antithesis term | What it means there | In this engine | Gap or caveat |
|---|---|---|---|
| `always(cond, message)` | must hold every time it is evaluated. The Java SDK also requires it to be reached at least once ("Assert that condition is true every time this function is called, *and* that it is called at least once") | `sim.always(id, cond)` | Ours passes if never reached, so it behaves like Antithesis' `alwaysOrUnreachable`. Pair conditional `always` checks with a `sometimes` on the guard |
| `alwaysOrUnreachable` | must hold whenever reached; passes if never reached | `sim.always` | same thing |
| `sometimes(cond, message)` | true at least once per test run; checks that tests exercise *meaningful scenarios* | `sim.sometimes(id, cond)` + `declaredSometimesIds`; the runner fails if a declared ID is never reached across all seeds | ours is per suite run (all seeds), like theirs per test run (the multiverse) |
| `reachable(message)` | this location is hit at least once per run | `sim.sometimes(id, true)` at the location | none |
| `unreachable(message)` | this location is never hit | `sim.always(id, false)` at the location | none |
| assertion **catalog** | every assertion declares itself at startup, so a never-reached `sometimes`/`reachable` can fail | `declaredSometimesIds` (explicit, up front) | `always` IDs are not cataloged, so an `always` that was never reached goes unnoticed |
| `message` → **property** | assertions with the same message aggregate into one property in the triage report | the `id` string | keep IDs stable; name them after the TLA+/Lean invariant |
| `alwaysGreaterThan`, `sometimesAll`, … | richer assertions that give Antithesis' guidance more visibility into values | none | not needed without a guided explorer |
| **deterministic hypervisor**, multiverse, timelines | full-system determinism; every event can branch a new timeline; any bug is perfectly reproducible | `VirtualClock`, the seeded `SimulationContext`, explicit step machines and hooks; a seed is one timeline | we have no branching and no guided exploration, only independent seeds. Discovery depends on the generator. |
| **fault injector** (network latency/partitions, node pause/kill, clock skips forward and backward, thread pausing) | continuous environmental faults at a chosen tolerance level (`none`, `resilient`, `high_availability`) | faults are explicit ops in the generator: failure, partial write, retry, kill between steps, duplicate delivery, `stepBackObservationClockNanos` | we inject only what the generator names. List the faults you didn't inject |
| **test composer** commands: `parallel_driver_`, `serial_driver_`, `singleton_driver_`, `anytime_`, `eventually_`, `finally_` | executables that Antithesis schedules into timelines; `eventually_`/`finally_` run with faults stopped, to check recovery and convergence | the op generator plays the drivers' role. Quiescent end-state checks play `eventually_`/`finally_`: drain everything, then check convergence | none |
| deterministic replay and debugging | rewind to any moment in a timeline | `SIM_SEED=<n>` plus the transcript hash, and a 200-step trace ring in every failure | none |

Sources:
- Assertions (types, evaluation, cataloging): https://antithesis.com/docs/properties_assertions/assertions/
- Java SDK `Assert` (exact `always` / `alwaysOrUnreachable` / `unreachable` semantics): https://antithesis.com/docs/generated/sdk/java/com/antithesis/sdk/Assert.html
- Faults and tolerance levels: https://antithesis.com/docs/environment/fault_injection/
- Test commands (`eventually_`, `finally_`, drivers): https://antithesis.com/docs/product/writing_tests/test_templates/test_composer_reference/
- How Antithesis works (multiverse, deterministic hypervisor): https://antithesis.com/docs/introduction/how_antithesis_works/
- DST overview (FoundationDB lineage, pluggable non-determinism versus hypervisor): https://antithesis.com/docs/resources/deterministic_simulation_testing/
