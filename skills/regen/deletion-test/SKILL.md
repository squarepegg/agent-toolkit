---
name: deletion-test
description: Use at design review, or when asked whether a module, service, package or component boundary is safe to delete and regenerate, is too big or too small, or "has the right grain". Scores the component on comprehension, isolation, mutation ownership and contract, asks what would prove a regenerated version correct, and returns a verdict (grain too coarse, too fine, about right) with the three highest-leverage fixes. Report only; changes nothing.
---

# Deletion test

Imagine `rm -rf` on this component and regenerating it from what survives. What would you rely on
to say the new one is correct? If the answer is "the old code", the code is doing the
specification's job.

Read `../_shared/conventions.md` first.

## Steps

1. **Scope.** Resolve the target to a component: a module, a package, or a named set of files.
   State the boundary you chose and why.
2. **Facts.** Run:
   `python3 <this-skill-dir>/../_shared/scripts/module_facts.py <paths...>`
   It reports size, concepts, interfaces, fan-in and fan-out, write sites and their callers,
   contract artifacts, and the tests that touch the target.
3. **Read.** Read every main file in the target, the tests the script listed, and the schema or
   migration files. Follow write callers into other modules. Mutation ownership is decided by
   who changes the state, not by where the SQL sits.
4. **Score** each dimension 0–3 with evidence, using `reference/rubric.md`. Never score without
   a `file:line` or a script fact.
5. **Oracle inventory.** List what would judge a regenerated version correct, tiered:
   durable (contracts, invariants, property tests, boundary tests), ephemeral (unit tests on
   internals), live (monitors, metrics). Then list what is missing. This answers "what would
   we rely on?"
6. **Verdict:** `grain too coarse`, `grain too fine`, or `about right`. Use the rubric's
   verdict rules. Rate deletion dread (trivial / mild inconvenience / existential) and say
   what causes it.
7. **Three fixes.** The three changes that most improve deletion safety. Each fix says what to
   do, which score it moves, and which skill drafts it.
8. **Report** at `docs/phoenix/YYYY-MM-DD-deletion-test-<slug>.md` using
   `../_shared/report-template.md`. Under 100 lines.

## Findings table

| dimension | score 0–3 | evidence | what would raise it |
|---|---|---|---|

## Example (abridged, from `examples/offers-database.md`)

| dimension | score | evidence | what would raise it |
|---|---|---|---|
| mutation ownership | 2 | sole writer `OfferStateWriter.upsert`, called only from `RecordDecision` (restate). The cooldown *rules* live in `restate` and `shared`, not here | none for this module; the concept spans 4 modules |
| contract | 2 | `V001__cooldown.cql` is the schema and a real-Cassandra test round-trips it, but `applyMigrations` loads that one file by name (`CassandraStore.kt:26`); a V002 would be silently skipped | apply every migration in order; test the applied set |

Verdict: **grain too fine**. A thin persistence adapter. The cooldown concept spans
`shared`, `database`, `restate` and `api-service`. Deleting this module is mild inconvenience;
deleting the cooldown concept is not.

## Known limitations

- `module_facts.py` is regex-based. Same-package usage without imports, DI wiring, reflection and
  SQL built at runtime can be missed. Confirm by reading.
- The 10-minute comprehension bar is a judgement call. Size is evidence, not the score.
- Mutation ownership across repos (another service writing the same table) is invisible unless
  that repo is checked out. Say "not checked" for other repos.
- The dread rating is the model's, unless the user gives theirs. Ask.
