# A/B plan: harness-engineering paper ideas → squarepegg skills (pre-registered, nothing run yet)

Source: arXiv 2609.00006 (Barbaste et al., Jul 2026), §9.6, §12.5, §16.

## agent-knowledge-map — no changes (the paper's ideas are already there)
| Paper idea | Already in repo |
|---|---|
| Size cap on the memory snapshot (Hermes) | `build_primer.py` primer_budget 8500 chars |
| Human-reviewed memory inbox (Gemini CLI) | `inbox/` + `brain.py compact` |
| Mine past sessions into candidates (Gemini/Hermes) | `mine_sessions.py` → inbox |
| Plain keyword search, no embeddings | `brain.rank` (idf-weighted lexical) |
| BM25 instead of current rank? | Test not worth running: current recall@1=0.969, recall@5=1.0 over 32 queries (`/tmp/abx/recall_all.py`). Best possible gain is 1 query. Log as a negative result |

## agent-toolkit — 2 hypotheses, tested live with fresh `pi --mode rpc`, graded only by scripts
H1 Load less up front (three tiers: index → SKILL.md → reference/): put dst (23K) and doubt (17K) on a diet,
   generated mechanically so the content is byte-identical (a unit test proves the split loses nothing).
H2 Rules as checks instead of prose (Obs. 3, Rec. 11): `dst/scripts/dst_lint.py` turns hard rules 3, 6 and 7 and the
   naming rules into checks (mode-named tests, no `bug catch`, no `random`/`time.time`/`uuid4` in sim code, replay
   check on, seed in failure output). SKILL.md tells the agent to run it.

Tasks (each is a small Python fixture repo with a planted bug):
- dst: a TTL cache with an invalidate-during-read race. Prompt: "add DST for this cache, then write the final report".
  Checks: suite runs; seeded through SIM_SEED; virtual clock; replay check; planted bug caught (failing seed or a
  known-bug pin); mode naming; the final report has the template's fields; dst_lint passes.
- doubt: a migration plan with a hidden irreversible step. Checks: a fresh-context reviewer was run (bash `pi -p`/
  `claude -p`); the loop stopped within the bound; the final answer names the flaw.

Arms: dst A (current) / B (H1) / C (H2); doubt A / B. 5 runs per arm → 25 runs. Est. $25–60, about 1–2 h in the background.

Adopt only if (decided before any run):
- H1: B's mean check pass rate ≥ A's AND B's median input tokens ≤ 0.9 × A's.
- H2: C has ≥ 30% fewer failed rule checks than A AND no other check worse.
- Otherwise drop the change and add a row to `negative-results.md` (evals/ in agent-knowledge-map, a new one in agent-toolkit).
5 runs per arm is a small sample: a draw counts as "not proven", not as a win.
