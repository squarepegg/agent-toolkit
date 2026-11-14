## Interaction with Other Skills

- **`code-review-and-quality` / `/review`**: complementary. `/review` is post-hoc PR verdict; doubt-driven is in-flight per-decision. Use both.
- **`source-driven-development`**: SDD verifies *facts about frameworks* against official docs. Doubt-driven verifies *your reasoning about the artifact*. SDD checks the API exists; doubt-driven checks you used it correctly under the contract.
- **`test-driven-development`**: TDD's RED step is doubt made concrete — a failing test is a disproof attempt. When TDD applies, that failing test *is* the doubt step for behavioral claims.
- **`spec-first`**: end-of-change counterpart. When the work was built from an accepted spec, verify the finished change with its phase C (mechanical evidence + fresh reviewer + `verdict.py`) instead of a doubt cycle on the whole diff; use doubt for the individual decisions along the way.
- **`debugging-and-error-recovery`**: when the reviewer surfaces a real failure mode, drop into the debugging skill to localize and fix.
- **Repo orchestration rules** (`../../references/orchestration-patterns.md`): this skill orchestrates from the main session. A persona calling another persona is anti-pattern B — see Loading Constraints above.

