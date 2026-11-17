# Deletion-test rubric

Each dimension scores 0–3. Cite evidence for every score.

## Comprehension: can a person or an LLM grasp its invariants, edge cases and transformations in about ten minutes?

| Score | Looks like |
|---|---|
| 3 | under ~500 LOC; a handful of concepts; invariants are stated in code, docs or tests |
| 2 | under ~1500 LOC, or small but with some unexplained scar tissue |
| 1 | needs a history tour: many special cases, `unknown` scars, behaviour spread across files |
| 0 | sediment: nobody can say what it must do without reading the commit log |

Inputs: `loc_total`, number of declarations, `scar_candidates`, and the unknowns from an
`implementation-memory-audit` report if one exists.

## Isolation: can its core behaviour be verified at its boundary without booting the system?

| Score | Looks like |
|---|---|
| 3 | core logic is pure or behind an interface; boundary tests run in-process with fakes |
| 2 | needs one real dependency (a container) but no other services |
| 1 | needs the app context plus more than one real dependency |
| 0 | only end-to-end or manual verification |

Inputs: the `heavy` column in the tests table, interfaces with fakes in tests, constructor
dependencies.

## Mutation ownership: does it have exclusive logical write authority over its data?

| Score | Looks like |
|---|---|
| 3 | exactly one logical writer, inside this component; others read through it or a read model |
| 2 | one writer, but the write *rules* live outside the component (it stores, someone else decides) |
| 1 | two writers in one codebase, coordinated by convention |
| 0 | several services or modules write the same state |
| n/a | holds no state; score 3 and say so |

Inputs: `write_sites`, `shared_writers`, `write_api_callers`. Follow callers. Concurrency
control (locks, single-writer actors, idempotency keys) counts as evidence.

## Contract: does it talk through a versioned, schema-enforced interface?

| Score | Looks like |
|---|---|
| 3 | versioned API, schema, or migrations; contract tests validate it at the boundary |
| 2 | typed interface (DTOs, a Kotlin interface) without versioning or contract tests |
| 1 | schema exists but nothing enforces it, or versioning is fragile |
| 0 | shared mutable classes, untyped maps, or "you have to know how the other side works" |

Inputs: `contract_artifacts`, `versioned_paths`, interfaces in `declarations`, and the
tests that assert wire shapes.

## Deletion dread (the gut check)

| Rating | Meaning |
|---|---|
| trivial | could be gone tomorrow; may be too fine-grained |
| mild inconvenience | the target zone |
| existential | a taproot: hidden invariants, implicit contracts or shared mutation |

Say what causes the rating. Dread is a technical signal, not a feeling to be argued with.

## Verdict rules

- **Too coarse**: comprehension ≤ 1 *and* (isolation ≤ 1 or ownership ≤ 1), or dread is
  existential. The fix is to split along a mutation or contract seam.
- **Too fine**: under ~300 LOC *and* the meaning lives elsewhere (logic in callers, a
  pass-through, rules outside the component, ownership 2 because "someone else decides"), or
  dread is trivial. The fix is to merge it with the component that holds the rules, or to
  accept it as an adapter and test the concept at the larger grain.
- **About right**: everything else, typically every score ≥ 2.

## Oracle inventory

| Tier | Counts as an oracle for a regenerated version |
|---|---|
| durable | contract tests on a wire or schema, property tests, invariants, boundary tests against real I/O |
| ephemeral | unit tests on internals or mocks: useful today, void after a rewrite |
| live | metrics, monitors and SLOs that would notice drift in production |

"What would we rely on" = the durable plus live rows. If that list is short, say so plainly.
