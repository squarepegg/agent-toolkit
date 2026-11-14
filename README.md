# agent-toolkit

Personal collection of agent skills, small utilities and benchmarks.

## Skills

| Path | What |
|---|---|
| `skills/adversarial-sdlc` | Spec → tests → design → build → adversarial attack → deterministic gate. Produces code that survived review and states residual risk. Has its own `README.md` (models, gate, Docker and optional Kubernetes backends), `docs/specs/` and `tests/`. |
| `skills/dst` | Deterministic simulation testing: seeded ops, virtual clock, fault injection, seed sweeps, model-vs-real conformance. Kotlin engine plus a Python sketch. |
| `skills/doubt` | Doubt-driven development: materialize a fresh-context reviewer biased to disprove before a non-trivial decision stands. |
| `skills/evidence-gated` | Evidence-gated decisions: criteria, computed checks, blind screen, deterministic verdict. |
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

# Claude Code: link each skill into ~/.claude/skills instead (spec-first finds `pi` or `claude` itself for its reviewer)
```

`skills/adversarial-sdlc` needs `pi` (or another CLI via `SDLC_RUNNER_<role>`), Python 3.9+, Docker or
macOS `sandbox-exec`, and for the Kotlin defaults JDK 21 and Gradle. Model gateway: `SDLC_GATEWAY_URL`
(see the skill's README). Unit tests: `python3 skills/adversarial-sdlc/tests/<name>_unit_test.py`;
some need `ast-grep` on `PATH`.
`skills/spec-first` needs only Python 3.9+, git and bash: `python3 skills/spec-first/tests/test_spec_first.py`, then
`python3 skills/spec-first/tests/planted_bugs.py` (CI runs both); `evals/run_evals.py` drives a live `pi` session and costs tokens.

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
