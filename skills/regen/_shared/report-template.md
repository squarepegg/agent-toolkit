# <Skill name>: <target>

| | |
|---|---|
| Repo | `<repo name>` @ `<short sha>` |
| Target | `<path(s)>` |
| Date | YYYY-MM-DD |
| Skill | `<skill-name>` |
| Inputs | scripts run, docs read, PRs fetched |

## Verdict

One or two sentences. The answer to the question this skill asks.

## Findings

Table. Columns are set by the skill (see its SKILL.md). One row per finding.
Every row cites evidence: `file:line`, commit SHA, PR #, or doc path.

## Proposed files

| File | Purpose | Status |
|---|---|---|
| `docs/phoenix/proposals/.../X.kt` | what it would guard | proposed, not applied |

Write "none" if the skill proposed no files.

## Unknowns

Things this run could not establish. Each one says what evidence would settle it and
who might hold it (team, channel, person, system).

## Limitations of this run

What the scan could not see: renamed or split files, squashed history, external repos,
runtime config, heuristic misses.

## Next

The single most useful next step, usually another skill: `run eval-tiering on <path>`.
