---
name: architecture-constraints
description: Use when turning architectural rules (layering, dependency direction, data ownership, "X must never call Y", config bounds) into executable fitness functions such as ArchUnit tests for JVM code, or when auditing existing ArchUnit/fitness tests for missing reasons. Every rule gets a provenance header (reason, who set it, the decision or incident behind it, what would make us revisit it) and the regeneration test "if this changed after regeneration, would we care?". Rules without a reason are flagged, not enforced. Produces proposed files only.
---

# Architecture constraints

Architecture is the set of constraints on how a system may change. A fitness function makes a
constraint push back. A constraint without a reason is Chesterton's fence with a build failure
attached.

Read `../_shared/conventions.md` first.

## Steps

1. **Collect candidate rules** from: ADRs and design docs; `implementation-memory-audit` and
   `deletion-test` reports; the user's statements; and structure the code already follows
   consistently. Record the source of each rule.
2. **Regeneration test** for each: *if a regenerated system changed this, would we care?*
   - `no`: an implementation choice. Drop it and list it under "Not architecture".
   - `yes`: write down **why**. The why becomes `Reason`.
   - `don't know`: ask the user. Do not invent a reason.
3. **Audit existing rules:**
   `python3 <this-skill-dir>/scripts/check_provenance.py <test-src-dirs>`
4. **Choose the mechanism** (`reference/archunit-kotest.md`):
   - static dependency or naming rule → ArchUnit;
   - config bound → config assertion test;
   - behavioural rule (idempotency, ordering) → hand it to `eval-tiering`. ArchUnit cannot see
     behaviour.
5. **Draft** each rule with the header from `reference/provenance-header.md`, into
   `docs/phoenix/proposals/YYYY-MM-DD-<slug>/`. A rule whose `Reason` is unknown is drafted
   **disabled** (`@Disabled("no provenance: …")`) in a separate `FlaggedConstraints` file.
6. **Verify the draft is true today.** Best: compile and run the drafts in a throwaway
   `git worktree`, and mutation-check each rule. **The mutation must be the failure the
   rule's `Reason` names**, not just any forbidden dependency. Example: a readiness rule whose
   Reason is "gating on the schema registry deadlocks rollouts" must fail when the registry is
   injected. The Offers draft missed exactly this; it was caught in review. Then
   check that every class named in the Reason is covered by the rule. At minimum, grep for
   current violations. A proposed rule that already fails is a
   finding: report it, and do not weaken the rule to make it pass. Then run
   `check_provenance.py --strict` on the drafts.
7. **Report** at `docs/phoenix/YYYY-MM-DD-architecture-constraints-<slug>.md`. List any test
   dependency to add (ArchUnit is not in any repo today: `com.tngtech.archunit:archunit:1.4.1`).
8. **Promote a flagged rule when its owner decides.** A flagged rule asks a question. Bring it
   back to the team; do not leave it disabled, because a disabled test checks nothing.
   - **Rejected:** delete the flagged rule, and note the decision where the repo records them.
   - **Confirmed:** write `Reason`, `Set by` (the person who decided, and the date) and
     `Source` from the owner's words, never your own. Move the rule into the main constraints
     file, enable it, and delete the flagged entry.
   - **Confirmed but failing today:** fix the code to obey the rule (on a branch, with the
     user's go-ahead). Do not weaken the rule. Mutation-check it against the old code, which must
     fail the rule and name the violating dependency. Add a test for any wiring the refactor
     moves: a dependency that silently stops being injected passes every unit test.
   - Run `check_provenance.py --strict` on the result.
   Offers example: rule 8 below was confirmed, and `EvaluateController`'s `RestateClient`
   call moved into `DecisionService`.

## Findings table

| # | rule | regeneration test | reason (source) | mechanism | holds today? | status |
|---|---|---|---|---|---|---|

Status: `proposed`, `flagged: no reason`, `not architecture`, `behavioural → eval-tiering`.

## Example (abridged, from `examples/offers-constraints.md`)

| # | rule | regeneration test | reason (source) | mechanism | holds? | status |
|---|---|---|---|---|---|---|
| 4 | `HealthController` depends on no downstream | yes: rollout deadlock | `HealthController.kt` KDoc | ArchUnit | yes; mutation check fails it | proposed |
| 8 | controllers do not use clients directly | don't know | none found | ArchUnit | **no**: `EvaluateController` imports `RestateClient` | flagged: no reason (disabled); later confirmed by the team and enforced after a refactor (step 8) |

## Known limitations

- ArchUnit checks bytecode dependencies only: no behaviour, no runtime config, no cross-service
  calls over HTTP.
- `check_provenance.py` finds ArchUnit rules and `@fitness`-tagged tests. Other fitness styles
  (Konsist, detekt rules, CI scripts) are not detected.
- Provenance is only as good as its source. "Set by" names the team or person recorded in
  git or docs; the skill does not verify that person still agrees.
- A rule that holds today may hold by accident. The header's "Revisit when" is the only defence
  against fossil constraints.
