---
name: compaction-pass
description: Use when asked to reduce complexity, simplify a module, remove unnecessary abstractions or layers, cut dependencies, or report on "conceptual mass". Inventories abstractions, interfaces, special cases, duplicated sources of truth and dependencies that do not earn their keep, and proposes removals ranked by risk with evidence. Excludes anything an implementation-memory-audit marked as scar tissue or unknown. Never applies a removal without explicit confirmation.
---

# Compaction pass

Conceptual mass is the number of things a person or a model must hold in their head to make a
safe change. Refactoring reorganises the closet; compaction asks whether you need the closet.

Read `../_shared/conventions.md` first. **Propose only. Apply nothing without the user
confirming each item by number.**

## Steps

1. **Memory first.** Find an `implementation-memory-audit` report for the target under
   `docs/phoenix/`. If there is none, run that skill first, or scan with `scar_scan.py` and
   treat every untraced site as `unknown, do not remove`.
2. **Facts:** `python3 <this-skill-dir>/../_shared/scripts/module_facts.py <paths...>`
3. **Inventory mass** with `reference/mass-checklist.md`: abstractions (single-implementation
   interfaces, pass-through layers, factories), special cases, duplicated sources of truth,
   nullable-for-tests parameters, dead declarations, dependencies.
4. **Ask of each item:** *does this concept justify its existence?* Give the rent it pays (a
   real second implementation, a test seam, an isolation boundary) or say it pays none.
5. **Rank** every proposal by risk (`reference/mass-checklist.md`, "Risk"). Anything marked as
   scar or unknown in step 1 goes in the **Excluded** table, with its audit row number.
6. **Report** at `docs/phoenix/YYYY-MM-DD-compaction-pass-<slug>.md`. For each proposal: what
   goes, what replaces it, the mass removed (concepts, not lines), the evidence, the risk, and
   the guard that must exist before applying it.
7. **Stop.** Ask which numbered items to apply. Apply only those, one commit each, after their
   guard exists.

## Findings table

| # | item | kind | rent it pays | proposal | mass removed | risk | guard first |
|---|---|---|---|---|---|---|---|

Then **Excluded (scar or unknown)**: item, audit row, why it stays.

## Example (abridged, from `examples/offers-clients.md`)

| # | item | kind | rent | proposal | mass | risk | guard first |
|---|---|---|---|---|---|---|---|
| 1 | defaults in both `VendorConfig` and `application.yml` | duplicated truth | none: YAML wins at runtime | YAML becomes the only source; env-var names unchanged | 1 concept | medium | config assertion test |
| 2 | `RestateClient(metrics: DecisionMetrics? = null)` | nullable-for-tests | none: production always passes it | make it non-null | 1 exception path | low | metric contract test |

Excluded: `contentId → id → offerId` fallback (audit #12, unknown). Considered and kept:
`ErrorHandlers` (0 textual references, but live through Micronaut `@Error`).

## Known limitations

- Usage counts are textual. DI, reflection, serialisation and other repos can use a symbol that
  looks unused. Check before calling anything dead.
- Mass is judged in concepts, not lines. Two reasonable reviewers can disagree; the evidence
  column is there so they can.
- Removals can hide in "rename and merge" diffs. Each applied item must be its own commit.
- It does not judge whether a scar's lesson is obsolete. That is a human call, recorded with
  `decision-capture`, before the item can move out of Excluded.
