# Evals: property-tests-from-examples

- Deterministic: `scripts/check_skills.py` (pins, budget, hand-offs in `tests/golden.json`) and
  `scripts/test_skill_tools.py`. `answer-pass.md` and `answer-fail.md` are real answers from the A/B, and they pin the
  grader both ways.
- Live A/B: `python3 scripts/skill_ab.py property-tests --samples 3`. Probe: "are these tests good enough?" on two
  example tests that a hard-coded or `price - pct` implementation passes.

Measured (2026-11-13, default model, 3 samples per arm, inside a personal agent setup):
| description | with skill | skill read | without |
|---|---|---|---|
| first version ("writing or reviewing unit tests …") | 1/3 | 1/3 | 0/3 |
| trigger-rich ("are these tests good enough", assertEquals, languages) | 3/3 | 2/3 | 0/3 |
The grader was loosened after reading the first answers (it missed "properties", "passes both", "reference", "never
goes up"). All stored answers in both arms were re-graded with the final grader; the numbers above use it.

Isolated `skill_ab.py` (2026-11-14, trigger-rich description, only this skill loaded vs none, 3 samples): **with 3/3 (skill read 3/3), without 0/3**, $0.58.
