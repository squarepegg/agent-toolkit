---
name: eval-tiering
description: Use when auditing or improving a module's tests, asking which tests would survive a rewrite, finding missing invariants, contract tests or property tests, or turning an implementation-memory-audit's unguarded findings into tests. Classifies tests as ephemeral, durable or live, applies the "would a rewrite in another language invalidate this suite?" check, and drafts the missing durable evaluations (Kotest property and contract tests by default) as proposed files. Never deletes or edits existing tests.
---

# Eval tiering

Evaluations are the real codebase. Find out which of this module's tests would still judge a
regenerated implementation, and draft the ones that are missing.

Read `../_shared/conventions.md` first. **Do not delete, rewrite or move existing tests.**

## Steps

1. **Inventory.** Run:
   `python3 <this-skill-dir>/scripts/list_tests.py . --for <main-path>` (tests anywhere that
   import the module), or pass test directories directly. It also lists live signals: monitors,
   k6 configs, emitted metrics.
2. **Classify each case**, not just each file, using `reference/tiers.md`. The script's tier is a
   suggestion. Read the assertions.
3. **Apply the rewrite check** to the suite: *if this module were rewritten in another language,
   which cases would still state a valid requirement?* A case whose requirement survives but
   whose harness does not counts as **durable intent, ephemeral harness**. Flag it for promotion.
4. **Find gaps.** Sources, in order:
   - `scar: unguarded` rows from an `implementation-memory-audit` report;
   - invariants stated in docs, ADRs or KDoc but not asserted anywhere;
   - pure functions with example tests only (property candidates);
   - wire shapes (requests, responses, events, tables) with no contract test;
   - **components that must agree** (a writer and a reader, a filter and a recorder) with no
     test that drives them together. Draft a reference-model property (`kotest-templates.md` §7);
   - **features that are off where they are deployed**: config-gated beans, flags or blank env
     defaults that no deployed config sets. Check the gitops values; propose a read-only check of
     a running pod or the data only with the user's approval (`../_shared/conventions.md`,
     "Verifying" step 5);
   - **durable suites that do not run in CI**: they skip when Docker, a sibling repo or a
     credential is missing, or CI never invokes them (`reference/ci-signal.md` §4);
   - business rules with a metric but no monitor (live gap).
5. **Draft** durable evaluations into `docs/phoenix/proposals/YYYY-MM-DD-<slug>/`, using
   `reference/kotest-templates.md`. Match the repo's runner: Kotest specs if the repo uses them;
   otherwise JUnit 5 with Kotest assertions and `kotest-property`'s `checkAll`. List any new
   test dependency. Each draft carries a header comment naming the requirement and its source.
6. **Verify and mutate** (files and git only; the standard is in `../_shared/conventions.md`,
   "Verifying proposed tests and rules"). In a throwaway worktree
   (`git worktree add /tmp/<repo>-wt HEAD`), copy the drafts into `src/test`, add any test
   dependency, and run only those tests. Check that each test *ran*. Then write a
   `mutants.json` of the bugs each draft exists to catch, aiming at boundaries, off-by-ones,
   missed resets and dropped fields, and run:
   `python3 <this-skill-dir>/../_shared/scripts/mutate.py mutants.json --results '<module>/build/test-results/test/*.xml' --watch 'Properties|ContractTest' -- ./gradlew :<module>:test`.
   Every draft must kill at least one mutant. Add mutants until `--watch` lists none. Report the
   kill table, and say which kills the old suite missed. Remove the worktree when done.
7. **If CI failures are called "flaky"**, triage them before drafting retries or timeouts
   (`reference/ci-signal.md`). Bursts across branches mean a moving input, not a flake.
8. **Report** at `docs/phoenix/YYYY-MM-DD-eval-tiering-<slug>.md` using the shared template.

## Findings table

| test (file › case) | tier | rewrite-safe? | protects | note |
|---|---|---|---|---|

Then a gap table:

| # | missing evaluation | kind | source of the requirement | draft file |
|---|---|---|---|---|

## Example (abridged, from `examples/offers-clients.md`)

| test | tier | rewrite-safe? | protects | note |
|---|---|---|---|---|
| `VendorRetryTest` › does not retry a read timeout | ephemeral harness, durable intent | no: calls `internal fun retryOnConnectionReset` with Micronaut exception types | 200 ms budget | promote to a wire contract: stub Vendor stalls, so exactly one request is observed |
| `VendorHttpClientTest` › request body contains only triggers | durable | yes, if run against the wire | deploy-order decision | keep |

| # | missing evaluation | kind | source | draft |
|---|---|---|---|---|
| 2 | a dropped pooled connection is retried once and succeeds | contract | audit #2, PR-B | `VendorWireContractTest.kt` |

## Known limitations

- Tier detection is heuristic: comments are stripped, but helper methods that hide mocks are not
  followed. Read before trusting a tier.
- "Survives a rewrite" is judged on the requirement, not the harness. Every Kotlin test needs
  porting. The question is whether it would still state a true requirement.
- Drafts are compiled and run only if step 6 is done. They are still proposals: the user moves
  them into `src/test`.
- A mutation check proves a draft catches the planted bugs, not every regression. Its value is
  in the mutants chosen, so name them in the report.
- Random property inputs miss exact boundaries unless the generator is biased towards them
  (`kotest-templates.md` §6). An unbiased property can pass with an off-by-one in place.
- Live evaluations are detected only if defined in the repo (Datadog entity, Terraform, k6).
  Monitors configured only in a vendor UI are reported as `not found in repo`.
