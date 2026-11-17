# Evals: proof that each skill is worth loading

Two tiers. Only the first runs in CI; nothing in CI calls a model.

| tier | proves | where | cost |
|---|---|---|---|
| **A. Advice witnesses** (`evals/witness/`) | the skill's advice is *correct*: following it catches a planted bug that ignoring it misses, with no false alarm on the fixed code | CI (`evidence.yml`) | free, deterministic, ~15 s |
| **A. Tool tests** (each skill's `tests/`) | the code a skill ships does what the skill says | CI (`skills.yml`, `skill-suites.yml`, …) | free |
| **A. Claims registry** (`evals/claims/`) | every claim is mapped to a witness, a tool test, an outcome task, or says why it can't be (policy) or isn't yet (gap) | CI | free |
| **B. Outcome tasks** (`evals/outcome/`) | the skill makes an *agent* do the work better: no skill vs skill vs same-length control, graded by what the agent's code does | laptop, results committed to `evals/outcome/ledger/` | tokens |
| smoke: probes (`skills/*/evals/probes.json`, `scripts/skill_ab.py`) | only that the skill's text reaches the model (keyword recall). **Not evidence of value** | laptop | tokens |

Current state of every skill: [`STATUS.md`](STATUS.md) (generated, and CI checks that it is current).

## A. Witnesses

Each witness (`evals/witness/w_*.py`) turns one quoted claim into an experiment with three arms:

| arm | code | advice | must |
|---|---|---|---|
| `ignored*` | buggy | not followed | **miss** the bug (so the advice is needed) |
| `followed` | buggy | followed | **catch** it (so the advice works) |
| `fixed` | fixed | followed | **pass** (no false alarm, not vacuous) |

If one arm misses its expectation, crashes, or fails for a reason other than the planted bug, the witness fails.
The dst and correctness-first witnesses run the dst skill's own engine (`skills/dst/templates/python/dst.py`), so they
test it too. For example, `dst-reentrant-hook`: 5,000 seeds of sequential ops never reach the stale-cache race, a
re-entrant hook finds it in 150 seeds, and the fixed cache passes.

```sh
python3 evals/witness/run.py            # all; one line per arm
python3 evals/witness/run.py --list
python3 evals/check_claims.py           # claims registry -> per-skill counts
python3 evals/test_evidence.py          # the runner, claims check and STATUS.md cannot be fooled
```

Adding a claim: quote the skill verbatim in `evals/claims/<skill>.json`. If the advice is checkable, write a witness
with all three arms. If it is a convention, mark it `policy` with a `why`. If it should be proven but isn't yet, mark it
`gap` with a `why`; gaps show up in STATUS.md. If the quoted text changes, CI fails until the claim is re-mapped. Every
`## Hard rules` item needs a claim.

## B. Outcome tasks (laptop)

A task (`evals/outcome/task_*.py`) is a fixture repo, a prompt, a scripted user, a deterministic grader, and a
pre-registered `PREREG`: runs per arm, alpha, ceiling, and the primary checks. Graders are pinned both ways in CI: a
reference solution passes, and a plausible weak one fails (`test_outcome.py`).

| task | the agent must | primary checks |
|---|---|---|
| `dst` | build a sim that finds a race reachable only inside `store.read` | sim exists, detects the race, replays identically, `cache.py` untouched |
| `doubt` | review a 7-flaw migration plan before it runs | **flaw recall**: planted flaws named (fixed regexes, quoting the plan earns nothing); procedure (fresh reviewer, no external CLI) is recorded but not scored |
| `property-tests` | improve four example tests for a CSV row formatter/parser | tests pass on the real code and fail on 7 plausible wrong ones (one-line mutants the examples cannot see) |
| `correctness-first` | add tests that tell whether a log-structured key-value store is correct | **mutation score**: kills of 6 one-edit bugs, one per skill pillar (long runs, cold keys, hot keys, edge values, error injection, restart) |

Arms are fresh, hermetic `pi` sessions:
- `none`: no skill.
- `skill`: the skill, without its `evals/` and `tests/`.
- `control`: the skill's own name and description over generic careful-engineering text of the same length
  (`control.md`). This rules out "any extra prompt helps".

Verdict (`verdict.py`, recomputed in CI from the committed rows). Pass/fail tasks use the table below with Fisher's exact test.
A task with a `score` block in its `PREREG` (correctness-first, doubt) compares the **mean mutants killed** per arm (0 if the agent's tests fail on
the real code, it edited the code, or it did not finish) with an exact one-sided permutation test; WORTHWHILE needs the skill's mean above both
baselines at p ≤ alpha each, and CEILING means the no-skill mean is already ≥ 80% of the maximum:

| verdict | rule |
|---|---|
| `WORTHWHILE` | skill beats both none and control, one-sided Fisher exact p ≤ alpha against each |
| `CEILING` | none already passes ≥ ceiling: the task can't show value, so write a harder one |
| `FLOOR` | no arm passes a single run: the task can't show value, so recalibrate it |
| `NOT_WORTH` | skill ≤ none |
| `INCONCLUSIVE` | anything else |

With n = 5 per arm, only 5/5 vs ≤1/5 or 4/5 vs 0/5 clears alpha 0.05. A change to the skill, the control or the task
changes its hash, so older rows count as stale.

```sh
git commit ...                                                     # run.py refuses uncommitted skill/task/control files
mkdir -p results/outcome
EVAL_PI_ARGS='["--model","provider/id"]' nohup python3 evals/outcome/run.py --tasks property-tests > results/outcome/run.log 2>&1 &
tail -f results/outcome/run.log                                    # status; resumable, skips runs already in the ledger
python3 evals/outcome/verdict.py
python3 evals/status.py --write && git add evals/outcome/ledger evals/STATUS.md && git commit -m "evidence: ..."
```

Rough cost per full task (3 arms × 5 runs): property-tests / correctness-first about $3–6, doubt about $10, dst about
$30. Full transcripts go to `results/outcome/` (gitignored); the ledger holds trimmed rows (checks, cost, hashes, model,
git HEAD).

## Candidates (outside skills, skill changes)

`evals/run_candidate.py` (laptop) writes `evals/candidates/results/<id>.json`. Commit it; `eval.yml` re-runs only the
deterministic `evals/gate.py` on it. See the top-level README.

## What this does not prove

- A witness proves the advice is right on a small, constructed SUT, not that it covers your system.
- An outcome verdict holds for that model, those tasks and that n. A new model means new rows.
- `gap` claims are unproven. They are listed, not hidden.

## Attempts memory (what was tried, why, what happened)

`evals/memory/attempts.jsonl` is the append-only ledger of every skill/harness edit tried: recurring `problem`, `rationale`,
`predicted` and `observed` outcome, `model`, `verdict`, and `retry_because` when it re-tries a corner. `gate.py` and
`run_candidate.py` read it (idea from *Self-Improvement Memory Enables Continual Recursive Self-Improvement*, arXiv 2611.18856).
Set `problem` (and a `predicted` outcome in the ledger row) on a new candidate. Rule: a third attempt on the same
(skill, problem, model) after 2 informative failures is a `REPEAT` unless it states `retry_because`; `run_candidate.py` refuses it before spending tokens (exit 3).
Another model is new evidence, so it does not match. Experiment failures (unsound grader, confounded, invalid model) do not count.
`python3 evals/memory.py --check` validates the ledger and that `memory/REPORT.md` (backtest over real history, prediction calibration) is current.
After a gate run, append the printed ledger row. `negative-results.md` stays the human log.
