# Conventions for all Phoenix skills

Code is a cache. These skills make the things that must survive deletion explicit:
behaviour, durable evaluations, component boundaries, and the reasons behind decisions.

## Hard rules

1. **Report, never remove.** Skills write reports and proposed files. They do not delete or
   rewrite code or tests. Applying any proposal needs explicit user confirmation in the
   conversation.
2. **"unknown" beats a guess.** If git, PRs, docs and code comments do not state an origin
   or reason, write `unknown`. An inference must be labelled `inferred:` and say what it
   rests on.
3. **Evidence or it did not happen.** Every claim cites a file:line, commit SHA, PR number
   or doc path.
4. **Files and git only.** No services, databases or new infrastructure. `gh` is used read-only
   when authenticated.
   Exception, on explicit user approval only: a read-only check of a deployed system (a pod's
   config, a `SELECT`) to confirm a fact that git cannot show. Write `unknown` until then.
5. **Short declarative prose.** No filler, no praise, no hedging adjectives.

## Where output goes

All paths are relative to the root of the repo being analysed.

| Output | Path | Committed? |
|---|---|---|
| Report | `docs/phoenix/YYYY-MM-DD-<skill>-<target-slug>.md` | User decides |
| Proposed tests / rules | `docs/phoenix/proposals/YYYY-MM-DD-<target-slug>/` | User decides |
| Decision records | `docs/decisions/NNNN-<title>.md`, or the repo's existing `docs/adr/` if present | Yes, with the change |

Proposed source files sit under `docs/phoenix/proposals/` until the user moves them. They are
never placed in `src/` without confirmation, so they cannot break the build.

**Check `git check-ignore docs/`.** Some repos gitignore `docs/` (Offers does: design
docs live on author machines). Then reports stay local, which is fine. But:
- a citation to `docs/...` resolves only on that machine, so say so in the report's
  limitations;
- decision records must go to a tracked location or follow the repo's own convention
  (project notes, README). `new_decision.py` refuses an ignored directory unless told otherwise.

`<target-slug>`: last two path segments of the target, lower-case, `/` → `-`
(e.g. `api-clients`).

## Report format

Use `report-template.md`. Keep a report under ~150 lines. Put raw script output in an
appendix or leave it out. Do not paste it into the findings.

## Verifying proposed tests and rules

A proposal is only as good as its evidence that it can fail. Before calling a drafted test or
rule done:

1. **Run it in a throwaway `git worktree`**, and check that each test *ran*, not just passed.
   JUnit 5 silently skips an expression-bodied `@Test` that returns a value (`= runBlocking {…}`
   without `: Unit`).
2. **Mutation-check with `scripts/mutate.py`.** Plant the bug each draft exists to catch, one at
   a time. A draft that passes on its first run is suspect until a mutant aimed at it fails it.
   **Every draft must kill at least one mutant** (`--watch`). A `no-build` result is not a kill:
   fix the mutant.
3. **Aim mutants at boundaries and off-by-ones** (`<` vs `<=`, `>=` vs `>`, `+1` vs `+2`),
   missed resets, dropped branches and dropped fields. Random inputs rarely land exactly on a
   boundary, so property generators must be biased towards edges
   (`eval-tiering/reference/kotest-templates.md` §6). Without that bias, a boundary bug survived
   every property in the Offers run.
4. **Check the suite runs where it matters.** A test that skips when Docker, a sibling repo or a
   credential is missing passes CI having tested nothing. See `eval-tiering/reference/ci-signal.md`.
5. **Check the behaviour is switched on where it matters.** Green tests prove the code works
   *when configured*. A feature gated by config (`@Requires`, a feature flag, an env var whose
   default is blank) can be off in every environment. Read the deployed config where it lives in
   git (gitops values). Checking a running pod, or the data the feature should write, goes beyond
   hard rule 4: propose it, and run it only with the user's approval, read-only. In the
   Offers run, cooldown recording had never run in dev or prod: one unset env var, an
   empty table, and every cooldown test green.
6. **Report the kill table** (mutant → result → killed by), including what the old tests missed.
   That column is the evidence the drafts add value.

## Applying proposals

Only after the user confirms. Apply on a branch, run the full build, and open a PR whose body
inlines the evidence (kill table, test counts). Reports under a gitignored `docs/` cannot be
linked. Never weaken a test or rule to make it pass. A draft that fails today is a finding.

## Feeding lessons back

When a run teaches something the skill did not say, such as a failure mode, a missed check or a
better template, record it in this repo: a dated page under `docs/field-notes/`, then a change
to the skill text that would have prevented it. See the repo notes.

## Origin vocabulary

| Term | Meaning |
|---|---|
| origin | First commit where this behaviour appeared (pickaxe `git log -S`), not the last commit to touch it (`git blame`). |
| PR | Pull request that merged the origin commit. |
| incident | Named incident, ticket or production observation in the commit or PR text. `none found` if absent. |
| lesson | What the code protects against. Quoted or cited where possible, or `inferred:`, or `unknown`. |

## Skill chain

```text
implementation-memory-audit ─┬─> eval-tiering (lessons become durable tests)
                             ├─> architecture-constraints (lessons become fitness functions)
                             └─> compaction-pass (scar tissue is excluded from removal)
deletion-test ───────────────┴─> eval-tiering / architecture-constraints
decision-capture: whenever a trade-off is made, at any step
```
