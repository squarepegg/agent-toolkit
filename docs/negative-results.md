# Negative results

Changes an A/B did not prove. Logged so nobody retries them blind.

| date | change | eval | result |
|---|---|---|---|
| 2026-11-14 | dst: progressive disclosure (13 sections → `reference/sections/`, 22.9K → 5.6K chars) | `evals/harness-ab` dst, 5 runs/arm | Pass rate equal (75% vs 75%); input tokens 0.92× (bar 0.90). The agent reads the moved sections anyway. Not adopted |
| 2026-11-14 | dst: `dst_lint.py` + a hard rule to run it | `evals/harness-ab` dst, 5 runs/arm | Violations 24 → 0, lint clean 5/5 vs 0/5, but `replays_identically` 4/5 vs 5/5 (likely a flake). Rejected under the pre-registered rule; a re-run needs its own pre-registration |
