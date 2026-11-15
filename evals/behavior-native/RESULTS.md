# Native Codex comparison: no candidate earned adoption

Ran **60 real case trials** using fresh GPT-6.1 Sol and GPT-6.1 Terra agents at medium reasoning effort,
comparing the existing skill, one candidate overlay, and a length-matched generic control.
Every distinct development and held-out case was covered in each arm on each model. No existing skill was changed.

The native protocol was committed before any task trials (`a85804d`, then label-capitalization normalization in
`a502a4b`, also before task trials). Its experiment fingerprint is
`b7395bad69d53c7bb48fcf9f73d53aaa9eeb31f3a899511f8f8874601bb9f982`.
Models received project-local skill metadata and task requests; parent conversation, arm names and graders were withheld.
Stopping trials used two turns in one agent; handoff trials used a separate fresh agent/workspace receiving only STATE.md.

## Observed registered checks

These are observed counts, **not completed five-trial verdicts**. The original reporter correctly remains INCOMPLETE.

| Candidate | Model | Baseline | Candidate | Generic control |
|---|---|---:|---:|---:|
| Activation exclusions | Sol | 4/4 | 4/4 | 4/4 |
| Activation exclusions | Terra | 4/4 | 4/4 | 4/4 |
| Stopping boundaries | Sol | 1/2 | 2/2 | 0/2 |
| Stopping boundaries | Terra | 0/2 | 0/2 | 0/2 |
| Saved-state recovery | Sol | 0/2 | 1/2 | 0/2 |
| Saved-state recovery | Terra | 0/2 | 1/2 | 1/2 |
| Periodic revalidation | Sol | 2/2 | 2/2 | 2/2 |
| Periodic revalidation | Terra | 2/2 | 1/2 | 2/2 |

### What the traces establish

- **Activation:** baseline and both overlays correctly answered the one-off explanations and began interviews for
  explicit spec requests, including the near-miss held-out requests. No added benefit appeared on these cases.
- **Stopping:** the candidate improved Sol's exact response-boundary compliance on the held-out case. Terra still
  placed options after the question. All observed stopping failures were the structural `stop_after_question` check;
  no trial implemented code or accepted a spec before the owner's answer. Moving options is not by itself evidence
  of a better engineering outcome, and the small observed gain did not transfer across models.
- **Recovery:** the strict score is misleading as a measure of lost knowledge. Every failed recovery row failed only
  because the final label was "Browser smoke" rather than "Browser smoke test". All other checks passed: next action,
  exact passing/failing commands, unresolved owner decision, constraint, lack of shipping authorization, state persistence,
  save scope and read-only resumption. A separately labelled **post-hoc sensitivity check** accepting those equivalent
  check names passes **12/12**, versus 3/12 under the original matcher. Original scores are preserved; this diagnosis is
  not an independent validation or a replacement positive verdict. The candidate did not demonstrate a substantive
  handoff advantage.
- **Revalidation:** baseline already sampled the authoritative sources and detected the mismatches on both cases.
  One Terra candidate response checked sources a and b but omitted a from its reviewed list while identifying a as
  contradicted. No unique candidate benefit appeared.

## Why stop at 60 of the planned 120

This is a **post-hoc, conservative futility decision**, not a pre-registered early-stopping rule. No unrun result was
entered in the ledger, and the experiment is not relabelled as a completed n=5 study.

The registered rule required a candidate to beat BOTH comparators at one-sided Fisher p <= 0.00625, with n=5 per
arm and no held-out regression. For each of the eight task/model cells, the stopping analysis grants the candidate
every remaining success and both comparators every remaining failure. Even that best possible completion fails
the registered significance rule. Additional held-out, ceiling and cost guards could only make adoption harder.

| Candidate | Model | Best possible final p vs baseline | Best possible final p vs control |
|---|---|---:|---:|
| Activation | Sol / Terra | 0.5000 | 0.5000 |
| Stopping | Sol | 0.0238 | 0.0040 |
| Stopping | Terra | 0.0833 | 0.0833 |
| Recovery | Sol | 0.0238 | 0.0238 |
| Recovery | Terra | 0.0238 | 0.1032 |
| Revalidation | Sol | 0.0833 | 0.0833 |
| Revalidation | Terra | 0.2619 | 0.2619 |

The bound was first checked after the pilot, then all remaining distinct held-out cases were completed before
stopping redundant repetitions. Exhaustive-completion tests check the bound against every possible remaining
pass/fail total. This justifies **no adoption within this registered study**; it does not prove zero effect, that the
ideas cannot help elsewhere, or that a larger/more relevant study could not find value.

## Evidence and reproduction

- [Registered protocol](plan.json), [input/model registration](registration.json), [unaltered live ledger](ledger.jsonl).
- [Stopping proof](../behavior-native-analysis/futility.py), [saved bound and ledger hash](../behavior-native-analysis/stopping.json).
- [Diagnostic handoff responses](../behavior-native-analysis/handoff-responses.json), [post-hoc label audit](../behavior-native-analysis/audit.py).
- Full local response captures: ignored `results/behavior-native/transcripts.jsonl`. Generated workspaces and the
  manifest remain available locally for inspection; no personal brain content or credentials are committed.

```sh
python3 evals/behavior-native/native.py report
python3 evals/behavior-native-analysis/futility.py
python3 evals/behavior-native-analysis/audit.py
python3 evals/behavior-native/test_native.py
python3 evals/behavior-native-analysis/test_futility.py
python3 evals/behavior-native-analysis/test_evidence.py
```

CI verifies original input bytes, the real ledger, the conservative stopping bound and the saved diagnostic evidence
without calling a model. Native result collection itself uses the host's native agent interface, not the blocked CLI
or Fortis gateway.

## Limits and interruptions

This is a small synthetic suite, not production traffic or broad task coverage. Native tool-read telemetry and
attributable dollar costs are unavailable, so there is no cost-efficiency claim. Native agents retain platform
instructions and available global skill metadata; this is not the hermetic Pi transport. One Terra control agent
reported reading personal brain instructions before the project restriction, while reporting no personal-brain writes.
Treat that ambient-context exposure as a protocol limitation, not evidence of isolation.

After the first 24 trials, three next-batch agent starts hit the workspace spend cap before producing responses or
changing their workspaces. After the user requested continuation, model readiness was confirmed and those unused
fixtures were retried with fresh agents. Failed starts were not counted as task failures.

These tests evaluate four prompt overlays. They do not evaluate new enforcement commands, shared schemas,
workstream-completion evidence validation, or the broader proposal to encode project extension contracts in tools.
