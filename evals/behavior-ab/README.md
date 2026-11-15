# Behavioral changes inspired by socratic-tutor

Prospective tests of four narrow changes to existing Squarepegg skills. These are synthetic failure hypotheses,
not claims that Jane Street's skill improves this toolkit, or that these failures have occurred in production.
No candidate is installed. Source: [Jane Street's socratic-tutor](https://github.com/janestreet/agent_skills/blob/master/socratic-tutor/SKILL.md).
The candidate text is written here; no Jane Street instruction text is copied.

## What is compared

| Candidate | Existing baseline | Candidate change | Primary case checks |
|---|---|---|---|
| activation | spec-first | Explicit activation exclusions in description and body | Read skill on an explicit spec request, avoid loading it for a one-off explanation, fulfill either request |
| stopping | spec-first | One owner question followed by a turn boundary | Two scripted turns; one question mark per response; response ends at the question; no implementation or accepted spec |
| recovery | brain | Explicit evidence, constraints, blockers and next-action handoff | Save state, then answer correctly from that state in a fresh process/workspace; failures remain failures and unrun checks remain unrun |
| revalidation | brain | Bounded sampling even when age and anchors look current | Catch a mismatched claim, no false alarms, evidence for reviewed claims, two source calls maximum |

Each arm retains the baseline protocol and supporting files. `baseline` is byte-exact; `candidate` changes only the
named behavior; `control` adds generic careful-engineering instructions of the candidate's character length.
For activation, discovery-description length is also matched; the control keeps the baseline description verbatim.
Character length is a practical control, not proof of identical tokenization.

Every process sees the skill through Pi's normal `--skill` discovery, with global skills, context, MCP and extensions
disabled. The explicit provider extension still loads. Activation cases never tell the agent to load the skill.
The other cases explicitly select the relevant workflow to isolate the tested behavior.

Baselines and their upstream commits/file hashes are in `baselines/provenance.json`. Brain CLI/lint support is copied
into each fixture and uses fixture-local `BRAIN_DIR`; the real personal brain is not configured as a source.
Only `STATE.md` crosses the recovery boundary. The second session receives no first-session chat or answer.
The recovery evidence is a supplied work log: this tests faithful handoff, not whether the reported checks really ran.

## Pre-registered decision

`plan.json`, `candidates.json` and `tasks.py` define the experiment before model runs. There are five repetitions per
arm: 150 case episodes total, including 30 paired recovery episodes (180 fresh agent sessions, plus preflight).
Each candidate has development and held-out cases; activation includes positive and negative cases in both splits.
Do not tune against held-out results. A later change requires a new registration and experiment.

`WORTHWHILE` requires all of:

- Candidate beats both baseline and control on suite passes, using the existing outcome harness's one-sided Fisher test.
- Alpha is 0.0125 (0.05 / four candidate hypotheses). At five repetitions, only very large gains can qualify.
- No held-out case-count regression against either comparator.
- Reported candidate cost is at most 1.5 times baseline cost. Missing provider prices block adoption.

An 80% baseline suite pass rate produces `CEILING`: this task cannot establish added value. Other verdicts include
`FLOOR` (no arm ever completes a suite), `NOT_WORTH`, `INCONCLUSIVE`, `REGRESSION`, `TOO_COSTLY`, `COST_UNVERIFIED`, `INCOMPLETE`, and `NOT_RUN`.
`NOT_WORTH` applies only to these tasks and this model, not to every possible version of an idea.
The falsifier for each hypothesis is in `plan.json`.

The budget is $30 in reported experiment cost, checked after each serial episode; the final episode may overshoot.
Preflight cost and partially completed/failed requests are not reported by this transport and are additional.
If prices are missing, execution stops after the first recorded episode. Transport failures stop the experiment
without being recorded as candidate failures. A wall timeout kills the worker process group, including a stuck Pi.

## Run

Requires Python 3.9+, git, Pi, PyYAML for the brain lint CLI, and access to the registered model/provider. CI calls no model.
The isolated Pi configuration does not inherit provider credentials: use the explicit gateway extension or supply
your normal provider authentication through the environment. Never copy credentials into this directory.

The checked-in registration pins Pi, model arguments, provider-extension hashes and all experiment/shared-harness
inputs. Changes to those inputs block a run. Working files must equal their committed HEAD bytes, including inputs
hidden from git status. It must be committed before execution. Pi is 1.5.2 in the initial registration.
If your installed provider or Pi version differs, create a new registration **before** collecting observations:

```sh
python3 evals/behavior-ab/run.py register --pi-args '["-e","~/.pi/agent/git/github.com/FortisGames/pi-fortis-ai-gateway","--model","fortis-ai-gateway/claude-sonnet-5-5","--thinking","medium"]'
git add evals/behavior-ab
git commit -m "evals: register behavioral candidate experiment"
```

Run from a terminal with gateway access; the command resumes recorded episodes with the same experiment hash:

```sh
mkdir -p results/behavior-ab
nohup python3 evals/behavior-ab/run.py run > results/behavior-ab/run.log 2>&1 &
```

Check progress and recompute the result:

```sh
tail -20 results/behavior-ab/run.log
python3 evals/behavior-ab/run.py report
```

Trimmed observations go to `evals/behavior-ab/ledger.jsonl`; full local transcripts go to ignored
`results/behavior-ab/transcripts.jsonl`. Commit the real ledger and update `RESULTS.md` after completion. Do not
commit fake-agent output, global configuration or credentials. Do not use `worker` directly for live experiments;
it is the isolated subprocess interface used by the registered runner.

## Offline validation and limits

```sh
python3 evals/behavior-ab/test_behavior_ab.py
```

CI also recomputes the committed ledger verdicts without requiring Pi or the local provider extension.
The offline suite checks exact baseline/control construction, snapshot provenance, verdict permutations and
corruptions, reference-vs-naive graders, and the actual shared RPC transport with a deterministic fake executable.
Fake runs are transport/grader tests, never evidence of candidate value. Existing outcome and harness-ab tests remain separate.

Limitations:

- The cases are deliberately small and synthetic. Five repeated runs on two to four scenarios establish only a
  narrow model-specific result; repetitions do not create broader task diversity.
- Skill loading is inferred from read/shell calls. An agent can follow a discovery description without loading the
  body; these tests count that as a missed activation on explicit spec requests. Unusual shell loading may be missed.
- Stopping checks are structural: they cannot reliably distinguish a compound question with one `?` from one decision.
  Review full transcripts for that semantic failure before promoting any winning stopping candidate.
- Fact evidence is logged by a local source interface. The grader checks the budget, consistency and unchanged facts/source;
  it does not provide an adversarial sandbox against an agent deliberately forging the source log.
- Selection by least-recent verification is tested for one upkeep cycle. No scheduling/coverage guarantee is implied.
- The harness disables ambient context discovery, but Pi's shell itself is not an OS sandbox. Run these synthetic fixtures
  in the same trusted local environment used for the existing outcome harness.

Preserve losing results. Promote an overlay into the actual skill only after a qualifying live verdict and inspection
of its transcripts. A free tooling test passing does not justify adoption.
