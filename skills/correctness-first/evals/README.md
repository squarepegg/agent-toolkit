# Evals: correctness-first-design

- Deterministic: `scripts/check_skills.py` (pins, budget, hand-offs in `tests/golden.json`) and
  `scripts/test_skill_tools.py`. `answer-pass.md` and `answer-fail.md` are synthetic fixtures that pin the grader
  both ways.
- Live A/B: `python3 scripts/skill_ab.py correctness-first --samples 3`. Probe: `probes.json` ("write the Correctness
  section for profile merge/unmerge"). To pass, an answer must name gray failure, by-construction guarantees, skewed
  inputs, faults and a conservation invariant.

Measured (2026-11-13, default model, 3 samples per arm, run inside a personal agent setup with other context loaded,
not the isolated `skill_ab.py`): with the skill 3/3, without 0/3. Without the skill, answers listed reasonable
invariants but never covered gray failure, by-construction guarantees, skewed inputs or the conservation check.

Isolated `skill_ab.py` (2026-11-14, only this skill loaded vs none, 3 samples): **with 3/3 (skill read 3/3), without 0/3**, $0.68.
