# Phoenix SDLC skills

Six Claude Code skills that make Claude safer to run on mature code. Based on Chad Fowler's
[Phoenix Architecture](https://chadfowler.com/regenerative-software/) series.

Code is a cache. What must survive deleting it: the **behaviour spec**, the **durable
evaluations**, the **component boundaries**, and the **reasons behind decisions**. These skills
find what is missing and draft it. They do not build a regeneration platform.

## Rules every skill follows

- **Reports and proposals only.** No skill deletes or rewrites code or tests on its own.
  Applying anything needs your confirmation.
- **`unknown` beats a guess.** Origins and reasons are cited (`file:line`, SHA, PR #) or
  marked `unknown` / `inferred:`.
- **Files and git only.** No services or databases. `gh` is used read-only when logged in. The one
  exception: a read-only check of a deployed system (a pod's config, a `SELECT`), and only when
  you approve it.

Details: [`_shared/conventions.md`](_shared/conventions.md). Report skeleton:
[`_shared/report-template.md`](_shared/report-template.md).

## When to use which

| Moment | Skill | Ask Claude | Output |
|---|---|---|---|
| **Before a refactor, cleanup, rewrite or regeneration** | `implementation-memory-audit` | "audit the scar tissue in `<path>` before I refactor it" | table of retries, timeouts, catches and special cases, each with origin, lesson, guard, and status (`guarded` / `unguarded` / `unknown, do not remove`) |
| **Before a refactor**, after the audit | `eval-tiering` | "tier the tests for `<path>` and draft what's missing" | each test classified ephemeral / durable / live, the rewrite check, and drafted Kotest property and contract tests |
| **Design review**: boundaries, new module, "should we split this?" | `deletion-test` | "run the deletion test on `<module>`" | scores for comprehension, isolation, mutation ownership and contract; verdict (too coarse / too fine / about right); top three fixes |
| **Design review**: rules we want enforced | `architecture-constraints` | "turn our layering rules into ArchUnit tests" / "audit our fitness functions" | ArchUnit and config assertions, each with a provenance header; rules without a reason flagged, not enforced |
| **PR time**, or whenever a trade-off is made | `decision-capture` | "record why we chose X" | one-screen `docs/decisions/NNNN-*.md`, linked by a `Decision:` commit trailer |
| **Periodically**, or when a module feels heavy | `compaction-pass` | "compaction pass on `<path>`" | removals ranked by risk; scar tissue excluded; nothing applied until you pick items |

Typical chain on a mature module:

```text
implementation-memory-audit → eval-tiering → architecture-constraints → (refactor) → compaction-pass
          deletion-test at design review;  decision-capture whenever something is chosen
```

## Worked example

All six were run on `example-service` (Vendor/Restate clients, cooldown storage). See each
skill's `examples/` folder. Highlights:

- The audit traced 17 behaviours to their commits and PRs, including a production incident (PR-B:
  "~6 fail-open events over ~7h"). Six had a known lesson but no test.
- eval-tiering drafted 16 cases. Each was compiled and run in a throwaway worktree, then
  mutation-checked: removing the retry wrapper, the 401/403 branch or the per-entry catch
  fails exactly one new test, while all 26 existing Vendor client tests stay green.
- architecture-constraints drafted 7 rules that hold today. It also flagged one rule with no reason
  that **fails today** (`EvaluateController → RestateClient`), drafted disabled.
- The runs corrected each other. eval-tiering found two audit rows that were wrong (a header-case
  bug in the scanner), and the audit was fixed in place.
- Follow-up PRs applied the rest (a handful of PRs). The cooldown property tests caught 18 of 19 planted
  bugs, 6 of which every existing example test missed. The flagged rule was confirmed by the
  team and enforced after a refactor. "Flaky CI" turned out to be an upstream repo moving.

What each run taught is folded back into the skill text above; re-read the skill before a new run.

## Scripts

| Script | Used by | Does |
|---|---|---|
| `_shared/scripts/scar_scan.py` | audit, compaction | finds scar-tissue candidates; traces each with `git log -S` (with and without `--follow`), `git log -L`, merge-PR lookup, `gh pr view`; lists tests and docs that mention it |
| `_shared/scripts/module_facts.py` | deletion-test, compaction | size, concepts, interface implementations, fan-in/out, write sites and their callers, contracts, tests |
| `eval-tiering/scripts/list_tests.py` | eval-tiering | per-file framework, cases, coupling vs boundary signals, suggested tier, rewrite answer; live signals |
| `_shared/scripts/mutate.py` | eval-tiering, architecture-constraints | plants one bug at a time (exact text replacement), runs the tests, reads JUnit XML: `killed` / `survived` / `no-build`; `--watch` lists drafts that killed nothing; refuses ambiguous patterns, needs a green baseline, always restores |
| `architecture-constraints/scripts/check_provenance.py` | architecture-constraints, CI | parses provenance headers; `--strict` fails on any rule enforced without a reason |
| `decision-capture/scripts/new_decision.py` | decision-capture | `new` / `check` / `list` records; follows existing ADR folders; refuses gitignored folders |
| `decision-capture/scripts/commit-msg` + `install_hook.sh` | decision-capture | opt-in hook that validates `Decision:` trailers (warn by default; `PHOENIX_DECISION_STRICT=1` blocks) |

All scripts are stdlib Python 3.9+ (the hook installer is POSIX sh) and read-only, except
`new_decision.py new`, the hook installer, and `mutate.py`, which edits source files temporarily
and restores them after each run.

## Install

Skills load from `.claude/skills/` in the project Claude Code starts in, or from
`~/.claude/skills/` for all projects.

```sh
# all projects (symlinks; updates follow this repo)
./install.sh            # → ~/.claude/skills/
# one repo only
./install.sh /path/to/repo/.claude/skills
```

The installer links each skill folder and `_shared/`. Skills reach their scripts through
`../_shared`, so keep `_shared` next to them.

## Known limits (all skills)

- The scanners are regex heuristics, Kotlin and JVM first. Every skill has a manual reading
  step, because structure (ordering, locks, bean conditions, absent code) is invisible to them.
- Git, PRs and repo docs are the only evidence. Slack, Datadog and incident tools are not
  searched, so "none found" means none found *there*.
- Some repos gitignore `docs/` (Offers does). Reports there stay local, and decision records
  need a tracked home.
- Judgement calls (the 10-minute bar, deletion dread, conceptual mass) are the model's unless
  you give yours. The evidence columns are there so you can disagree.
