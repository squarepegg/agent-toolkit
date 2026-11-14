---
name: correctness-first-design
description: Use before building or testing a new service, feature or stateful/distributed component (identity graphs, event pipelines, queues, caches, workflow engines, multi-tenant APIs), when writing a spec or design doc, or when asked "how do we test this", "how will we know it works", "is it correct", "load/chaos test". Produces invariants, gray-failure signals and an invariant-checking workload with faults, then hands execution to deterministic-simulation-testing.
---

# Correctness-first design

Quality is not a gate at the end. Before any code exists, decide what "working" means and how you will know it is working. Tests then cover only what the design can't rule out by construction.
Source: Sean Allen (Antithesis) on The Pragmatic Engineer, youtube OrXKvOjR-vI, 40:52 / 49:30 / 80:14.

## Where this fits
| Step | Skill |
|---|---|
| Decide what "correct" means: invariants, the workload, signals | **this skill** |
| Turn it into a checkable spec (R/E/C ids, lock, independent verify) | `spec-first` |
| Run the workload in-process: seeded ops, virtual clock, faults, `always`/`sometimes` | `deterministic-simulation-testing` |
| One pure function, no state across calls | `property-tests-from-examples` |
| Choose between designs | `evidence-gated-decisions` |

## 1. Preflight (in the spec, before code)
Answer each question in writing. A blank answer is a finding.
1. **Invariants.** What must always hold? Write each one as a checkable statement with a stable id, such as `OneProfilePerIdentity` or `EveryAcceptedEventStoredOnce`, or "a response only carries the caller's data".
2. **Rule out by construction.** Which invalid states can the types, schema or architecture make impossible? Options: sealed types or a state enum that allows only legal transitions, keys and constraints, idempotency keys, no shared mutable state. Only what is left gets tested.
3. **Down vs gray failure.** How will we detect the system being down? How will we detect a **gray failure**: health checks pass, but many users get a bad result? Name the metric or alert for each.
4. **Unknowns.** Which parts do we not understand yet? Do a throwaway spike, a few hours to 2 days, before committing to a design.
5. **Terms.** Give each loaded word one testable definition. For example, exactly-once *delivery* is not exactly-once *processing*. Send any fork to the owner.

## 2. Workload (the main test)
A workload = **operations + input distribution + checker (+ faults)**. It is not a list of scripted examples.
- **Operations.** Cover the full API, including the admin and delete paths.
- **Input distribution.** Set key count and range, key and value sizes, hot keys and skew, and **long runs of the same operation** (10k puts in a row force compaction, resharding and backpressure). Don't pick operations uniformly at random. That's a "drunken walk": it never strays far from the start state and never reaches the interesting code.
- **Checker.** Run invariant checks **concurrently** with the operations, plus a final check once the system is quiescent.
- **Faults.** Run again while you kill processes, restart consumers, add lag or rebalances, and partition or slow the store. Bugs show up under faults, not on a laptop.

Template, the **bank test** (isolation and conservation): N accounts hold a fixed total. Workers make random transfers inside transactions. A reader keeps summing the balances, and the total must never change. Adaptations:
- **Identity graph:** the number of entities and the set of identifiers they own are conserved across merge and unmerge.
- **Pipeline:** for each id, events in = rows stored + rows rejected. Nothing is lost and nothing is duplicated.
- **Multi-tenant API:** tag each request with a unique marker and assert that the response carries the same marker, under concurrency and cancellation.

## 3. Hand-off to `deterministic-simulation-testing`
Keep the names identical so the design and the test suite stay one artifact:
| Here | In the DST suite |
|---|---|
| invariant id | `always("<id>", ...)` |
| rare state you want reached (a long run, a hot key, a fault at the last step) | declared `sometimes("<id>", ...)` |
| input distribution | the op generator (DST step 4) |
| fault list | faults as ops (DST step 3) |
| bank-test style total | reference-model or differential oracle (DST step 5) |
If correctness lives in the infrastructure rather than in your code, use a real-infra or Jepsen-style test instead. DST step 1 has the decision table.

## 4. Output
- **With `spec-first`:** each invariant becomes `- R<n>: Always: <invariant>`, and each gray-failure signal becomes an `R` or `E` item. Each one gets a covering check, e.g. ``- C<n> [R<n>]: `<sim or workload command>` -> exit 0``. Don't add extra sections; spec-first warns on unknown ones.
- **Otherwise:** add a `## Correctness` section to the design doc with:
  - an invariants table: invariant | enforced by construction? | workload check | prod signal
  - the workload: ops, distribution, checker, faults
  - the gray-failure signals

If an invariant has no check and no prod signal, it is unverified. Say so.
