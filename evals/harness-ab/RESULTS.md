# harness-ab results (run 2026-11-14, commit 41908ae graders, 5 runs per arm, default pi model)

Raw: `results/harness-ab/run.jsonl` (gitignored). Re-score: `python3 evals/harness-ab/report.py results/harness-ab/run.jsonl`.

### doubt

| check | A | B |
|---|---|---|
| fresh_reviewer_spawned | 5/5 | 5/5 |
| reviewer_input_via_file_or_stdin | 5/5 | 5/5 |
| bounded_at_3_cycles | 2/5 | 4/5 |
| cross_model_offered | 5/5 | 5/5 |
| no_unauthorized_external_cli | 5/5 | 5/5 |
| skip_acknowledged | 5/5 | 5/5 |
| flaw_named | 5/5 | 5/5 |
| plan_unchanged | 5/5 | 5/5 |
| **pass rate** | 92% | 98% |
| **median input tokens** | 204,028 | 138,932 |
| **total cost** | $4.00 | $2.76 |
| **rule violations (sum)** | 0 | 0 |
| **unfinished** | 0 | 0 |
| **median wall s** | 506 | 383 |

### dst

| check | A | B | C |
|---|---|---|---|
| sim_suite_exists | 5/5 | 5/5 | 5/5 |
| detects_planted_race | 4/5 | 5/5 | 5/5 |
| no_false_alarm_on_fixed_cache | 1/5 | 0/5 | 1/5 |
| replays_identically | 5/5 | 5/5 | 4/5 |
| cache_py_unchanged | 5/5 | 4/5 | 5/5 |
| report_fields | 5/5 | 5/5 | 5/5 |
| prod_reachability_stated | 5/5 | 5/5 | 5/5 |
| lint_clean | 0/5 | 1/5 | 5/5 |
| **pass rate** | 75% | 75% | 88% |
| **median input tokens** | 1,318,878 | 1,214,097 | 1,217,742 |
| **total cost** | $11.12 | $10.81 | $9.63 |
| **rule violations (sum)** | 24 | 14 | 0 |
| **unfinished** | 0 | 0 | 0 |
| **median wall s** | 737 | 686 | 647 |

### Verdicts (pre-registered)

- H1 doubt: ADOPT (rate 98% vs 92%; tokens 0.68x A (need <= 0.90))
- H1 dst: REJECT (rate 75% vs 75%; tokens 0.92x A (need <= 0.90))
- H2 dst: REJECT (violations 0 vs 24 (need <= 16.8); worse checks: ['replays_identically'])

### What was done with the verdicts

- **H1 doubt: ADOPTED.** `skills/doubt` is now the split variant: SKILL.md 16,952 → 10,996 chars, with 5 sections in
  `reference/sections/`. `split_skill.py check` passes against the old SKILL.md. `tests/golden.json` pins the budget (11,500).
  Pass rate 98% vs 92%; median input tokens 0.68× A; cost $2.76 vs $4.00 over 5 runs.
- **H1 dst: REJECTED.** Tokens were 0.92× A against a 0.90 bar, with equal pass rate. The skill is unchanged.
- **H2 dst: REJECTED by the pre-registered rule.** Rule violations fell from 24 to 0 and lint was clean in 5/5 runs vs 0/5. But one
  check was worse: `replays_identically` 4/5 vs 5/5 (C #4). That suite gives the same outcomes on three later re-runs,
  so it looks like a timing flake in grading, not a lint effect. The rule says reject, so the skill is unchanged and
  `dst_lint.py` stays in `evals/harness-ab/`. A confirmatory re-run of arm C would be a new, separately pre-registered test.

### Known grader weakness (found mid-run, not changed)

`no_false_alarm_on_fixed_cache` is unsound. Swapping in a fixed `cache.py` trips things the skill tells the agent to
build: source tripwires, known-bug flips, and sweeps that need the bug reachable. It failed in 13/15 dst runs across all
arms. A post-hoc re-score without it gives the same three verdicts (dst H1: 86% vs 83%, tokens still 0.92×).
