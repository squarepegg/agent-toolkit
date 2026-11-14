# harness-ab: do two ideas from the harness-engineering paper make these skills better?

Source: Barbaste et al., *Harness Engineering: Anatomy, Architecture, and Evolution of Coding Agents* (arXiv 2609.00006, July 2026).
Plan and thresholds were written before any run: `docs/ab-harness-paper-plan.md`.

| Hypothesis | Arm | What changes |
|---|---|---|
| H1 load less up front (index → SKILL.md → linked files, §12.5) | B | `scripts/split_skill.py` moves every section except the core into `reference/sections/` and leaves one pointer line per section. Lossless by construction, and `check` proves it |
| H2 rules as checks instead of prose (Obs. 3, Rec. 11) | C (dst) | `dst_lint.py` (D1–D6: nondeterminism, mode naming, known-bug pins, replay on, anti-vacuity, mutant catches assert) plus a hard rule to run it |

Tasks (`tasks.py`) are fresh `pi --mode rpc` sessions with no context files, extensions or other skills, in a throwaway git repo.
A scripted user gives the same answer in every arm.
- **dst**: a read-through cache where an update during `store.read` leaves a stale value cached for the TTL. Only an
  interleaving inside the read reaches it. *Detected* means the agent's suite gives different per-test outcomes on the
  buggy cache and on a fixed one.
- **doubt**: a migration plan that drops a column before deploying the code that stops reading it.

Grading is deterministic: files, exit codes, unittest outcomes, tool-call commands, substrings. The graders have tests
of their own (`test_harness_ab.py`): a reference suite passes every dst check, a sequential-only suite does not detect
the race, and each lint rule catches its planted bug.

```sh
python3 evals/harness-ab/test_harness_ab.py                       # free; CI runs it
nohup python3 evals/harness-ab/run.py --n 5 --jobs 3 > results/harness-ab/run.log 2>&1 &   # costs tokens
tail -f results/harness-ab/run.log
python3 evals/harness-ab/report.py results/harness-ab/run.jsonl   # table + pre-registered verdicts
```

Results: see `RESULTS.md`.
