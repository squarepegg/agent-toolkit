# Evals: tests-as-data

- Deterministic: `python3 skills/tests-as-data/tests/test_tests_as_data.py` (guard matrix as a table, runner behaviour,
  planted-bug checks done by hand: 4 mutants of `guard.py` / `table_runner.py`, all caught), plus
  `scripts/check_skills.py` (pins, budget, hand-offs in `tests/golden.json`) and `scripts/test_skill_tools.py`.
  `answer-pass.md` and `answer-fail.md` pin the grader of the first probe both ways.
- Live A/B: `python3 scripts/skill_ab.py tests-as-data --samples 3` (costs tokens).
- Probes: an intermittent golden test with a retry-and-re-bless proposal; a mechanical check that a refactor did not weaken tests.

Measured (2026-11-14, default model, only this skill loaded vs none, 3 samples per arm, ~$1.90 per run of 3 probes):
| probe | with skill | without |
|---|---|---|
| flaky-golden (run twice, fix the source, no retries, human approves) | 3/3 (read 3/3) | 0/3 |
| refactor-guard (per-task file check, test data untouched) | 3/3 (read 3/3) | 0/3 |
| eval-ship ("8/10 correct, does it work?") | 2/3 (read 1/3) | 2/3 |

`eval-ship` was **removed**: the baseline already answers it (no headroom) and the skill was read in 1 of 3 runs, so
the section 6 advice is unmeasured. `refactor-guard` is partly circular: its first group accepts the skill's own
`guard.py` / "task type" wording, which a skill-less model cannot produce; read 0/3 vs 3/3 shows the skill is used, not
that the advice is better than a good reviewer's. The grader of this probe was loosened once after reading the first
answers (3 of 3 correct answers said "guard script" / "per task"); all stored answers were re-graded. Raw answers: not committed.
