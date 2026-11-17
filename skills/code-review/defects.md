# Defect classes to hunt

A catalog of what a reviewer should reliably look for. A class is a place to look, never a finding on its own: a finding names the line, the evidence and a real input or scenario. Add a class whenever a human review catches something this list missed.

## Code
1. **Error disappears.** Catch-all swallows, failure logged but exit 0, a parser falls through to a default instead of rejecting. What does a timeout look like to the caller?
2. **Default nobody sets.** Env var or constant with a fallback where nothing in the repo or deploy config sets the real value.
3. **Silent truncation.** Hard-coded page size or limit, first page only, SQL `LIMIT`. What does the caller get past the limit?
4. **Pattern matches more than intended.** Regex, prefix, `LIKE`, equality. Produce a real input that slips through.
5. **Two runs at once.** Cron, jobs, queue consumers or workflows doing the same work; what serialises them (lock, concurrency policy, idempotency key)?
6. **Same value in two places.** Constants, topic or table names, casing, ids duplicated. Name both locations.
7. **Past a limit the repo already enforces.** Lint, size or nesting rule defined elsewhere; quote it and the measured number.

## Data / platform
8. **Schema drift.** A field added, renamed or retyped on one side of a contract only; a sensitive-field flag missing.
9. **Not idempotent / replay-unsafe.** Backfill, consumer or workflow handler that double-counts or double-charges on retry.
10. **Environment bleed.** Dev and prod contexts, credentials, hosts or buckets mixed in one change.
11. **Secrets or personal data** in logs, config, fixtures or error messages.
12. **Deploy config mismatch.** Image tag, secret, role or resource limits not matching the code change.

## Process
13. **Test proves the mock.** Assertions that pass whatever the code does, or no test for the failure path.
14. **Claims without evidence.** The description says tested or verified with nothing to show.
15. **Change outruns the review.** One PR mixes unrelated risk domains, so the risky part gets the attention of the boring part (see triage `split`).
