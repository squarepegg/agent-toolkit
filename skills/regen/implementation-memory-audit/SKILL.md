---
name: implementation-memory-audit
description: Use BEFORE refactoring, cleaning up, simplifying, rewriting, migrating or regenerating existing code, and whenever asked to remove "unnecessary" retries, timeouts, null checks, try/catch blocks, fallbacks, special cases or magic numbers. Finds scar tissue in a module and traces each piece to the commit, PR and incident that introduced it, then proposes a test or constraint that preserves the lesson. Not for new code.
---

# Implementation memory audit

The implementation remembers lessons the organisation forgot. Find them before changing the code.

Read `../_shared/conventions.md` first. Rules that matter most here: never remove code, write
`unknown` rather than guess.

## Steps

1. **Scope.** Resolve the target to paths in one git repo. Add the config that feeds it
   (`application.yml`, `*.properties`, env defaults) and any sibling file that calls the same
   helpers.
2. **Scan.** Run:
   `python3 <this-skill-dir>/../_shared/scripts/scar_scan.py <paths...> --format json > /tmp/scar.json`
   Add `--max 120` for large modules. Use `--pr off` if `gh` is slow or unauthenticated.
3. **Read the code.** The scanner is a regex heuristic. Read every target file yourself and add
   the sites it missed (see `reference/patterns.md`, "Scanner blind spots"). Mark those
   `found by reading`.
4. **Trace each site.** Follow `reference/tracing.md`. Read the origin commit body, the PR body
   and any doc the scanner listed. Do not trust `git blame` for the origin.
5. **State the lesson.** Use one of three forms only:
   - `stated: "<quote>" (<source>)`
   - `inferred: <claim> (basis: <evidence>)`
   - `unknown`
6. **Check the guard.** Open each test the scanner listed. Mark the guard `pinned` (a test fails
   if the behaviour goes), `mentioned` (a test touches it but would not fail), or `none`.
7. **Propose a guard** for every site whose guard is not `pinned`. Name the kind: property test,
   contract test, ArchUnit rule, config assertion, or monitor. One line each. Do not write it.
   `eval-tiering` and `architecture-constraints` write it.
8. **Set a status:**
   - `scar: guarded`: lesson known, a test pins it.
   - `scar: unguarded`: lesson known, nothing pins it.
   - `unknown, do not remove`: no traceable reason. This is the default when in doubt.
   - `incidental`: traced, and the origin shows it is plain implementation (e.g. a cast
     suppression). List these in one line, not in the table.
9. **Write the report** using `../_shared/report-template.md` at
   `docs/phoenix/YYYY-MM-DD-implementation-memory-audit-<slug>.md`. Include a "Tensions"
   section when two lessons, or a lesson and a doc, pull in opposite directions.

## Findings table

| # | location | what it does | origin (commit · PR · incident) | lesson | guard today | proposed guard | status |
|---|---|---|---|---|---|---|---|

Group adjacent lines that implement one behaviour into one row.

## Example (abridged, from `examples/offers-vendor-clients.md`)

| # | location | what it does | origin | lesson | guard | proposed guard | status |
|---|---|---|---|---|---|---|---|
| 1 | `VendorHttpClient.kt:207-221` | retries only resets/closed-before-response, never `ConnectException` | `commit-B` · PR-B · prod: ~6 fail-opens/7h | stated: "fresh-dial failures … a retry just burns the 200ms hot-path budget" (commit-B) | pinned (`VendorRetryTest`) | none needed | scar: guarded |
| 12 | `VendorHttpClient.kt:114-117` | offer id falls back `contentId` → `id` → `offerId` | `commit-A` · PR-A · none found | unknown. Vendor's DTO sends only `contentId` | none | contract test against Vendor's response schema | unknown, do not remove |

## Known limitations

- The scanner is regex. It misses scar tissue expressed as structure: ordering, locks, bean
  conditions, absent code. Step 3 exists for this.
- Pickaxe finds the first commit where the text appeared. Reformatting, renames into new
  identifiers, and copy-paste across files can hide the true origin. Squashed history loses
  detail.
- PR-A-style initial imports and AI-written commit bodies can look authoritative but only say
  *what*, not *why*. Treat them as weak evidence.
- Incidents outside git (Slack, Datadog, PagerDuty) are invisible. The report says
  `none found`, not `none`.
- It does not judge whether a lesson still holds. That needs a human.
