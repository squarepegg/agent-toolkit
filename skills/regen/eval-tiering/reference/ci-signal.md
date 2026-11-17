# Is the CI signal trustworthy?

A durable test only counts if it runs and its failures are readable. Check this before you
trust a suite or call a failure "flaky".

## 1. Triage failures before calling them flaky

Pull the last ~100 runs: `gh run list --workflow <file> --limit 100 --json databaseId,conclusion,headSha,headBranch,createdAt,attempt`.
Then fetch each failure's log (`gh run view <id> --log-failed`) and classify it:

| Pattern | Meaning | Fix |
|---|---|---|
| Fails, then passes on rerun of the **same SHA** | flaky | find the race or timeout |
| **Bursts across several branches**, then all green after one commit | the environment changed under CI (an upstream repo, image or runner) | pin the moving input; test it separately (§3) |
| Every run on one branch fails, other branches pass | that branch is broken | not a CI problem |
| `initializationError` or setup-only failures | the environment never came up | read the cause chain (§2) |

In the Offers run, 13 of 76 failures looked flaky. 8 were one cause: a sibling repo (Vendor)
built from `main` dropped a column the test seed wrote. The failures came in bursts, not reruns.

## 2. Make failures say what broke

- Full exception format on every Test task (`kotest-templates.md` §8).
- Wrap fragile setup steps (seeding another service's database) so the error names the likely
  cause and the exact upstream revision under test.
- If logs are not enough, upload test reports as an artifact on failure.

## 3. Pin moving inputs, test the moving edge separately

If CI builds or checks out another repo at a branch, a change there breaks every open PR at
once. Pin a SHA for PRs (one-line file, bumped by PR). Add a scheduled run against the branch
head, plus `workflow_dispatch` with a ref input, so drift is caught within a day without
blocking anyone. GitHub emails scheduled-run failures only to whoever last changed the cron
line. Add a team alert if that is not enough.

## 4. No silent skips

List the tests that report `skipped` in CI. Each skip must be intended. A suite that skips
when its environment is missing must fail in CI instead (`kotest-templates.md` §8). Also check
that skip logic runs before anything that can fail first, such as eager dependency injection of
a bean that exists only with the environment.
