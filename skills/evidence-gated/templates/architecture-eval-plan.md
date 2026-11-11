# Architecture eval: <system> (plan, <date>)

## Why

<Which patching pattern triggered this: the corner, the repeated findings, the count of findings caused by earlier fixes. Patching inside the current shape only picks the best patch. This eval starts from **requirements and workload**, not from the current design, and lets checks decide.>

## Anti-bias rules

1. **Requirements before designs.** Requirement list and invariant suite are written, reviewed by the owner and **committed** before any candidate is modelled.
2. **Every requirement carries a source tag** (product, law, vendor-contract, consumer, ops, old-behaviour-only, new-design-only). `old-behaviour-only` rows are candidates to drop; the owner decides.
3. **Clean-sheet designers never see the current design,** only requirements and workload facts. The current design is one candidate among others, not the baseline.
4. **Designer, modeller and attacker of an option are different agents,** from at least two model families.
5. **Deterministic verdicts only:** `scripts/eval.py` (TLC, data thresholds, simplicity lint), a blind screen, "(Recommended)" only on DECISIVE.
6. **Each option states its falsifier** before it is evaluated.

## Phases

| # | Phase | Output | Who |
| 0 | **Requirements baseline.** Extract from proposal, specs, old code, docs, Slack, Notion, ClickUp. Tag sources. Raise owner questions. | `requirements.md` (template) | agent, then owner review |
| 1 | **Workload facts.** Rates (mean, peak), data shape, rare-event frequencies, deletes, backfills, vendor calls, read QPS and latency, state size. | `workload.md` + the queries | agent (read-only queries) |
| 2 | **Invariant suite.** Store-independent properties from phase 0 (e.g. erased data never returns; no cross-subject leak; opt-out wins; no lost events), over an **observation interface**. A **reference model** is used only to derive mutants, never as the oracle for candidates. One mutant per property. Past counterexamples become scenarios. | `Requirements.tla` + mutants | modeller agent; committed before phase 3 |
| 3 | **Candidates.** Clean-sheet designs from requirements and workload only; at least 3 independent designers; plus the current design and the obvious store options. | `candidates/<id>.md` | designer agents, mixed families |
| 4 | **Models.** Each candidate modelled against the suite using **declared primitives only** (e.g. exactly-once state, at-least-once side effects, single-partition CAS, multi-row transactions). Crashes, retries and reordering allowed. | `<id>.tla` | modeller agents (not the designer) |
| 5 | **Eval.** TLC per invariant and mutant; simplicity lint; data checks (cost, latency, size, throughput); ledger check (which recorded findings each candidate makes moot or reintroduces). | decision yaml → `results/` | `eval.py` + `screen.sh` |
| 6 | **Attack.** An adversarial attacker per surviving candidate, with TLC access. Each finding becomes a scenario in the suite; re-run the eval. | reviews | attacker agents, different from designer and modeller |
| 7 | **Decision.** Owner (and platform team) decide from the table. Hard-to-reverse choices get a blind independent check. | decision record | owner |

## Gates

- Phase 2 is committed before phase 3 starts.
- Phase-5 thresholds are committed before the phase-1 data is applied to them.
- A candidate the attackers break twice in the same corner is dropped or redesigned, not patched.

## What happens to current work

<What finishes, what pauses until the decision, what is independent and can merge.>
