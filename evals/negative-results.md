# Negative results (candidates that did not earn a place)
Append newest first. `evals/gate.py` prints the line to paste. Drop or revert a rejected candidate after logging it.
Re-try one only with new evidence: harder probes, a different model, or a changed claim.

Format:
- YYYY-MM-DD | candidate (source) | probes x samples, model | dev without -> with, heldout, cost | VERDICT
2026-11-14 | dst: progressive disclosure, 13 sections moved to reference/sections (arXiv 2609.00006, harness-ab) | 5 runs x 1 task, default pi model | checks 75% -> 75%, input tokens 0.92x (bar 0.90), cost $11.12 -> $10.81 | NO_DELTA
2026-11-14 | dst: dst_lint.py + hard rule to run it (arXiv 2609.00006, harness-ab) | 5 runs x 1 task, default pi model | rule violations 24 -> 0, lint clean 0/5 -> 5/5, but replays_identically 5/5 -> 4/5 (likely grading flake); pre-registered "nothing else worse" | REJECT (rerun needs its own pre-registration)
