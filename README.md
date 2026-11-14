# agent-toolkit

Personal collection of agent skills, small utilities and benchmarks.

## Skills

| Path | What |
|---|---|
| `skills/adversarial-sdlc` | Spec → tests → design → build → adversarial attack → deterministic gate. Produces code that survived review and states residual risk. Has its own `README.md` (models, gate, Docker and optional Kubernetes backends), `docs/specs/` and `tests/`. |
| `skills/dst` | Deterministic simulation testing: seeded ops, virtual clock, fault injection, seed sweeps, model-vs-real conformance. Kotlin engine plus a Python sketch. |
| `skills/doubt` | Doubt-driven development: materialize a fresh-context reviewer biased to disprove before a non-trivial decision stands. |
| `skills/evidence-gated` | Evidence-gated decisions: criteria, computed checks, blind screen, deterministic verdict. |
| `skills/correctness-first` | Correctness-first design: before code, write invariants, what is enforced by construction, down vs gray-failure signals; design an invariant-checking workload (ops + skewed inputs + concurrent checker + faults, bank-test template) and hand it to `dst` / `spec-first`. |
| `skills/property-tests` | Property tests from examples: the dumbest implementation that passes your tests, then round-trip / invariant / idempotence / oracle / metamorphic properties. |
| `skills/spec-first` | Interview the user into a checkable spec (code first, one decision at a time, defaults marked assumed), then verify the change with a fresh reviewer that sees only spec + diff + raw output; scripts decide PASS / FAIL / NOT CHECKED. Tests: `tests/test_spec_first.py`, `tests/planted_bugs.py`; live evals: `evals/run_evals.py`. |

Each skill is a directory with a `SKILL.md`, and optionally `roles/`, `scripts/`, `templates/`,
`reference/` and `examples/`.

### Install

```sh
# pi / Agent Skills hosts
ln -s "$PWD/skills/adversarial-sdlc" ~/.agents/skills/adversarial-sdlc
ln -s "$PWD/skills/dst"               ~/.agents/skills/deterministic-simulation-testing
ln -s "$PWD/skills/doubt"             ~/.agents/skills/doubt-driven-development
ln -s "$PWD/skills/evidence-gated"     ~/.agents/skills/evidence-gated-decisions
ln -s "$PWD/skills/spec-first"         ~/.agents/skills/spec-first
ln -s "$PWD/skills/correctness-first"  ~/.agents/skills/correctness-first-design
ln -s "$PWD/skills/property-tests"     ~/.agents/skills/property-tests-from-examples

# Claude Code: link each skill into ~/.claude/skills instead (spec-first finds `pi` or `claude` itself for its reviewer)
```

`skills/adversarial-sdlc` needs `pi` (or another CLI via `SDLC_RUNNER_<role>`), Python 3.9+, Docker or
macOS `sandbox-exec`, and for the Kotlin defaults JDK 21 and Gradle. Model gateway: `SDLC_GATEWAY_URL`
(see the skill's README). Unit tests: `python3 skills/adversarial-sdlc/tests/<name>_unit_test.py`;
some need `ast-grep` on `PATH`.
`skills/spec-first` needs only Python 3.9+, git and bash: `python3 skills/spec-first/tests/test_spec_first.py`, then
`python3 skills/spec-first/tests/planted_bugs.py` (CI runs both); `evals/run_evals.py` drives a live `pi` session and costs tokens.

### Checks and evals (all skills)

- `python3 scripts/check_skills.py`: deterministic, stdlib only, run in CI. It checks frontmatter, unique names, a
  README install line for each skill, no private strings, and, where `skills/<dir>/tests/golden.json` exists: pinned
  text, a size budget, and hand-offs to other skills (each one must exist and still be named in the SKILL.md).
  `--self-test` plants one bug per check in a temp copy and fails if any survives.
- `python3 scripts/skill_ab.py <dir> [--samples 3]`: live A/B (costs tokens). It answers `skills/<dir>/evals/probes.json`
  in fresh `pi` sessions, once with only that skill loaded and once with none, and grades with fixed substring groups,
  never a model. Results go to `results/` (gitignored). Measured deltas are in each skill's `evals/README.md`.
  `--with-path DIR` loads another skill in the `with` arm (same probes); `--out FILE` sets the results file.
- `python3 scripts/split_skill.py split|check <skill_dir> <out_dir>`: progressive disclosure for a long SKILL.md. Keeps the
  core sections, moves the rest to `reference/sections/` with one pointer line each; `check` proves the split is lossless.
  `skills/doubt` is built this way (A/B-proven, see below).
- `evals/harness-ab/`: pre-registered live A/B of two ideas from arXiv 2609.00006 (load less up front; rules as a lint
  script). Tooling tests run in CI (`python3 evals/harness-ab/test_harness_ab.py`); the live run costs tokens. Verdicts:
  `evals/harness-ab/RESULTS.md`; rejected changes: `evals/negative-results.md`.

### Adopting something new: candidate -> eval -> deterministic gate

| Workflow | When | What | Model calls |
|---|---|---|---|
| `skills.yml`, `eval-gate.yml`, `skill-suites.yml` | push/PR | skill checks; `evals/test_gate.py` (verdicts, registration, properties, `skill_ab` -> gate through `evals/fake_pi.py`); `scout/test_scout.py`; the adversarial-sdlc and evidence-gated suites | none |
| `eval.yml` | PR labelled `eval`, or by hand | each added/changed `evals/candidates/<id>.json`: `evals/run_candidate.py` then `evals/gate.py`; verdict commented on the PR | yes: variable `EVAL_MODEL`, secret `ANTHROPIC_API_KEY` |
| `scout.yml` | Mondays | `scout/scout.py`: GitHub, HN, arXiv (`scout/sources.json`), minus anything already seen, ranked by keywords + stars gained per week (from the star snapshot hidden in the previous issue) ; papers listed separately from Semantic Scholar searches (last 6 months, most cited first, at least 10 citations), as a `scout` issue. Optional secret `S2_API_KEY` avoids the shared rate limit | none |

1. Copy `evals/candidates/_template.json` to `<id>.json`: source, a one-sentence claim, the skill whose probes test it,
   an optional outside skill pinned to a full commit sha (`fetch` + `with_path`), samples, thresholds.
2. Commit it before running. The gate refuses a candidate file that differs from HEAD, probes that changed since the
   run, and an unpinned model.
3. Open a PR and label it `eval`. Verdicts: `ADOPT` (exit 0); `CEILING`, `NO_DELTA`, `REGRESSION`, `TOO_COSTLY`,
   `INCONCLUSIVE` (exit 1). Probes marked `"split": "heldout"` must not regress and are never tuned against.
4. Not adopted: paste the printed line into `evals/negative-results.md` and drop the change.

Local: `EVAL_PI_ARGS='["--model","provider/id"]' python3 evals/run_candidate.py evals/candidates/dst-skill.json`, then
`python3 evals/gate.py evals/candidates/dst-skill.json results/dst-skill.json`.

## Snippets

| Path | What |
|---|---|
| `snippets/board_to_pdf.py` | Drive a real browser, log in once, print a canvas app (Miro) board to PDF. |
| `snippets/copy_board.py` | Copy board items through the REST API when native export is disabled. |
| `snippets/run.sh` | One-shot venv + Playwright wrapper for `board_to_pdf.py`. |
| `snippets/grafana-okta-export.cjs` | Guided SSO login → read-only export of one Grafana org via its provisioning API → logout. |

No browser profiles, cookies, exports or board PDFs are stored here.

## Restate

| Path | What |
|---|---|
| `restate/register-kafka-subscriptions.sh` | Idempotent Kafka→service subscription registration through the admin API; checks first, treats 409 as success, waits for admin readiness. |
| `docs/restate-operations.md` | Invocation triage, timeout settings, subscription ordering, and the metadata `data-loss` failure mode with recovery options. |

## Benchmarks

`benchmarks/cryptographic` is a JMH harness comparing BLAKE3 and BouncyCastle digests, with a
collision benchmark. Build with the Gradle wrapper; results are not committed.

## See also
Companion repo: [agent-knowledge-map](https://github.com/squarepegg/agent-knowledge-map), a map of everything your coding agents know, checked against the code and scored by evals.
