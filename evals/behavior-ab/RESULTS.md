# Behavioral candidate experiment: not run

No candidate has earned adoption. There are no live experiment observations and no live ledger.

| Candidate | Live verdict | Adopted |
|---|---|---|
| Activation exclusions | NOT_RUN | No |
| Stopping boundaries | NOT_RUN | No |
| Saved-state recovery | NOT_RUN | No |
| Periodic fact revalidation | NOT_RUN | No |

The pre-registered model is `fortis-ai-gateway/claude-sonnet-5-5`, thinking `medium`, on Pi 1.5.2.
The experiment defines baseline/candidate/generic-control arms, development and held-out cases, five repetitions,
and a $30 reported-cost stopping budget. See [the plan and run instructions](README.md).

## Live attempt

On 2026-11-15, after pre-registration commit `b7621be`, `python3 evals/behavior-ab/run.py run` exited 1:

```text
model preflight failed (exit 1); verify model/provider/gateway access
```

No case episodes started. Independent diagnostics found a gateway listening on `127.0.0.1:8080`, while two curl
attempts from this restricted session could not connect. This is an access blocker, not a candidate outcome.
The subsequent registration update adds portable offline ledger validation; model arguments and tasks are unchanged.

## Offline evidence

- `python3 evals/behavior-ab/test_behavior_ab.py`: 15 tests pass. These include generated verdict permutations,
  reference-vs-naive grader sensitivity, the actual shared RPC transport with a fake executable, fresh-workspace
  recovery, cost/ledger safeguards, committed-input enforcement, and portable reporting.
- `python3 evals/outcome/test_outcome.py`: 20 tests pass.
- `python3 evals/harness-ab/test_harness_ab.py`: 16 tests pass.
- Skill Creator `quick_validate.py`: all 12 generated arm skills pass validation.
- `python3 scripts/check_skills.py`: eight existing skills, zero failures.
- `python3 evals/status.py --check`: existing skill evidence status is current.

These checks validate tooling and instruction structure, not the effectiveness of the candidate instructions.

## Continue with gateway access

From the `agent-toolkit-behavior-ab` worktree in a normal terminal:

```sh
mkdir -p results/behavior-ab
nohup python3 evals/behavior-ab/run.py run > results/behavior-ab/run.log 2>&1 &
```

Then run `python3 evals/behavior-ab/run.py report`. Inspect the full transcripts before promoting any winning overlay,
commit real ledger rows, and replace this note with the measured table. Preserve inconclusive and losing results.
