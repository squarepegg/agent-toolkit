---
name: adversarial-sdlc
description: Use when building or changing a component that must survive adversarial review, when asked for spec-first / test-first development with an independent attacker, or "make this robust", "prove it", "harden this", "red-team the implementation". Runs separate, OS-sandboxed agent roles (spec, tests, optional design + judge, build, attack, bug planter) as headless processes. One gate script judges every step (tests, property tests, mutation score, static analysis, type checks). Every attack finding must come with a failing test. It ends with a report of what was verified, what was not, residual risks and an escalation list. Never claims bug-free.
---

# Adversarial SDLC

The goal is code that survives adversarial review, not code that is "bug-free". Each phase is a
separate role. Roles share nothing but files the orchestrator copies to them. One script,
`scripts/gate.sh`, decides pass or fail.

Run everything through `<skill>/scripts/sdlc.sh --project <repo> <command>`, where `<skill>` is
the directory of this file.

Before putting a trade-off to the user, use `evidence-gated-decisions` (eval table, no recommendation).

## Hard rules

1. **The gate is the only verdict.** Never say a step passed unless a `gate-report.md` on disk
   says so. Never edit gate.config, sdlc.config, tests or build files to make it pass.
2. **Stop at human decisions.** Exit 7 means a human must act: approve the spec, or rule on
   spec gaps. Show the user the file, and wait. Use `--relayed "<their exact words>"` only after
   they have actually decided. Never decide for them.
3. **No repro, no finding.** A finding counts only if its test compiles and fails on every run.
   `validate-findings` decides this, not you.
4. **Done means** that `REPORT.md` says `DONE` or `GATE PASSED — HUMAN REVIEW REQUIRED`.
   Anything else is not done. Give the "Not done because" lines verbatim.
5. **State residual risk.** The final answer always includes what was verified, what was not,
   residual risks and the escalation list, copied from `REPORT.md`. Do not soften them.
6. **Do not play a role yourself.** You orchestrate. If you write the code, the tests, the
   findings or the planted bugs, the isolation is gone.

## Steps

0. **Existing code?** If the change touches mature code and the Phoenix skills are installed,
   run `implementation-memory-audit` first. Put its `scar: unguarded` and
   `unknown, do not remove` rows into the requirements.
   - **Read the review history too:** inline review threads on the PRs that touched the target,
     resolved and bot threads included (`gh api repos/O/R/pulls/N/comments`). Put every edge case
     a reviewer raised into the requirements, as a rule or as an Open question. Edge cases left
     out of the requirements (for example an ambiguity on replay) came back as review comments
     after the port.
   - **The gate covers the project it runs on, nothing more.** If the project is a slice copied
     out of a larger repo (because, for example, the gate cannot run the host's framework), the
     report certifies the slice only. Code written to integrate it back is ungated: callers, and
     guards for behaviour across calls such as replay or validation. List that code as not
     verified, and state the cross-call invariants in the requirements even when the slice
     cannot test them. Then run the host repo's full test suite (with `--rerun-tasks`, and
     check the report timestamps) and get a human or bot review of the ported change.
1. **Init.** `sdlc.sh init`, or `init --scaffold kotlin --package <pkg>` for a new project. Set
   the per-role models in `sdlc.config` (`README.md`). Put the attacker on another model family.
2. **Spec.** Write the requirements to a file, then run `sdlc.sh spec <file>`. It exits 7. Show
   the spec and its Open questions, then **stop**. A new spec archives any earlier run.
   A human may edit `.sdlc/spec.md` before approving. `approve` then records the edit
   (`spec.edited`, `spec.edit.diff` against the role's `spec.role.md`), and the report lists it.
3. **Approve.** After the user approves, run `sdlc.sh approve --relayed "<their words>"`.
4. **Run.** `sdlc.sh run` does tests → [design] → build → up to 3 attack/fix rounds → planted-bug
   calibration after a dry round → report. It resumes where it stopped.
5. **Act on the exit code:**
   - 7, spec gaps: the tests role found gaps, contradictions or vacuous tests. Show
     `.sdlc/spec-gaps.md`. The user either amends the spec (back to step 2) or accepts the gaps
     (`accept-gaps --relayed`). Accepted gaps stay listed as not verified.
   - 6, tests rejected: the tests role wrote implementation, or its inventory did not match a
     real run against TODO() stubs. Show `.sdlc/tests-feedback.md`.
   - 3, dispute: show `.sdlc/disputes.md`. If the human upholds it, `repair-tests --relayed`
     sends it to a fresh tests role. That stops with exit 7 and `.sdlc/repair/review.md` (the diff,
     with removed inventory cases flagged). The dispute closes only on `accept-repair --relayed`.
     If the human rejects it, `resolve-disputes --relayed`.
   - 4, the gate still fails: show the last gate report. Do not fix the code yourself. If it says the
     mutation step **timed out**, that is a time limit, not a defect: the builder is not called again.
     The human decides how to make Pitest faster (`sdlc.pitestExcludedTests`, property iterations in
     `sdlc.pitestJvmArgs`, `sdlc.pitestThreads`, `SDLC_DOCKER_CPUS`) or raises the limit
     (`GATE_MUTATION_TIMEOUT`), then `relock --relayed`; the rerun gates the current code first.
   - 5, an attack round did not complete: this is not a clean round. A provider timeout usually
     means a lower `SDLC_THINKING_attack` or another model. Then rerun `run`.
6. **Report.** Give the Status line, the "Not done because" list if any, the four sections of
   `.sdlc/REPORT.md`, and the paths of the gate report and `findings.validated.json`.
   After each fix round that passes the gate, a `consolidate` role proposes deterministic checks (ast-grep rules, or a
   behaviour check staged for the next run) for the round's reproduced findings; `scripts/consolidate.py` validates them
   without a model (B2) and only STAGES accepted rules under `.sdlc/consolidate/round-N/staged-rules/`. Nothing is
   adopted until a human relocks a batch. Needs ast-grep; best effort; `SDLC_CONSOLIDATE=off` disables it;
   `sdlc.sh consolidate --round N` re-runs it.

## Isolation (what each role can reach)

Every role runs in a fresh copy that holds only its inputs, inside an OS sandbox
(`scripts/sandbox.sh`, required by default; backends below):
- writes go only to that copy;
- reads are limited to what is listed (Docker: only the mounts; `sandbox-exec`: an allowlist under
  `$HOME` and temp);
- every process gets a private Gradle home, pi agent dir and temp dir;
- the real project, `.sdlc/`, other workspaces and agent transcripts are denied.

Only the listed outputs are imported back. Main code must stay inside `GATE_MAIN_PACKAGES`.

| role | gets | output kept |
|---|---|---|
| spec | requirements, existing code | `spec.md` |
| tests | SPEC.md, build skeleton | test files, inventory, notes, test-only dependency list (stubs are checked, then discarded) |
| design / judge | SPEC.md (+ code) / candidates | candidates; `scores.json` + `decision.md` (validated) |
| build | spec, locked tests, last gate report, findings, own notes | main paths, notes, disputes |
| attack | SPEC.md, code, tests | `findings.json` (schema-checked) and repro files |
| referee | SPEC.md, each reproduced test and its failure | a verdict per finding; only `valid` repros are locked |
| plant | SPEC.md, code | planted bugs (never shown to an attacker) |

The authoritative gate runs on a snapshot, sandboxed. Attacker repros run sandboxed, in throwaway
copies. Everything except the main paths and docs is locked.

### Backends

| | |
|---|---|
| `SDLC_SANDBOX_BACKEND` | `docker`, `sandbox-exec` or `auto` (default: `docker` if usable, else `sandbox-exec`). `docker` never falls back: if Docker is unusable, `SDLC_SANDBOX=required` exits 97 |
| `SDLC_SANDBOX` | `required` (default), `auto` (warn, run unsandboxed), `off`. Unchanged |
| probe | `scripts/sandbox.sh --probe` prints `docker`, `sandbox-exec`, `inherited` or `none`. It is recorded per role in `roles.jsonl` and in each gate report, with the image IDs on Docker |

Docker setup, once: `scripts/docker/build.sh`. It builds `sdlc-gate`, `sdlc-role` and
`sdlc-egress`, records their IDs in `~/.cache/sdlc-docker/images.env`, and warms the read-only
Gradle cache volume `sdlc-gradle-ro`. Rebuilding changes the image IDs, hence `harness_sha`, which
invalidates harness-bound records (by design). `sdlc.sh` refills the cache (`scripts/docker/warmup.sh`)
from locked build files around the `tests` phase. Gate steps are offline.

Threat model: roles and code under test are adversaries. The human, the orchestrator, Docker, the
images and the model gateway are trusted. A malicious operator (that would need CI as the authority) and kernel or VM escapes (`runc`) are out of scope.

A container gets:
- only the mounts listed for the call, at the same paths, plus the skill directory read-only,
  `SDLC_SANDBOX_READ` read-only, and for model roles `SDLC_PI_EXTENSIONS` read-only;
- a read-only root filesystem, tmpfs home and temp, no capabilities, `no-new-privileges`, the host
  UID, and memory, CPU and pid limits;
- no credentials (no `auth.json`) and an environment allowlist;
- `--net local` or `none`: no network. `--net all`: an internal network whose only exit is the egress
  proxy, which forwards to the gateway only. On Docker, roles always get `all`
  (`SDLC_SANDBOX_NET_ROLES` is ignored; `roles.jsonl` records `sandbox_net`).

Operational notes:
- Docker Desktop shares `/Users`, `/Volumes`, `/private`, `/tmp` and `/var/folders`. Colima shares
  only `~`: set `TMPDIR` under `~`. An unshared path is detected, and the run stops with exit 97
  ("mount not shared with the Docker VM").
- Give the Docker VM at least 10 GB of memory. Tune with `SDLC_DOCKER_MEMORY` (4g),
  `SDLC_DOCKER_CPUS` (4) and `SDLC_DOCKER_PIDS`. The other knobs (`SDLC_DOCKER_IMAGE_*`,
  `SDLC_DOCKER_PASSENV`, `SDLC_EGRESS_ALLOW`, `SDLC_EGRESS_UPSTREAM`, `SDLC_DOCKER_HOST_ALIAS`,
  `SDLC_DOCKER_STATE`) are in `README.md`.
- Docker Desktop needs a paid subscription above 250 employees or $10M revenue. Colima and OrbStack
  use the same `docker` CLI.
- The model gateway is `SDLC_GATEWAY_URL` (`http://host:port`, default `http://localhost:8080`). Inside
  role containers it is set to the egress proxy's address, also under each name in `SDLC_GATEWAY_ENV`
  (space-separated), for provider extensions that read the URL from a differently named variable.

Residual risk lines in `REPORT.md` come from the backend each role actually used. Report them
verbatim. Docker: shared VM kernel, and model roles can send what they can read to the provider
through the gateway. `sandbox-exec`: no CPU or memory limits, and model roles keep their network.

## Relation to other skills

- `doubt-driven-development`: the attacker is its Step 3 made mechanical. It gets ARTIFACT and
  CONTRACT, never the CLAIM. RECONCILE becomes "repro or discard", with disputes and gaps going
  to a human. It keeps the 3-cycle bound and cross-model review (`SDLC_MODEL_attack`).
- Phoenix skills: mutation evidence, edge-biased generators, reference models, "no-build is not
  a kill", no silent skips, `unknown` over guesses, decision records. See `reference/lineage.md`,
  which says which rules are mechanical and which are prompts only.

## Known limits

- Without a usable backend (no Docker, not macOS) `required` (the default) refuses to run; `auto`
  runs unsandboxed and the report says so. Docker has been developed on Docker Desktop only;
  Colima and Linux are unverified. The `sandbox-exec` backend limits files, not the network, and
  each role can read its own copy of the pi credentials. Docker roles hold no credentials.
- A reproduced finding proves the code disagrees with a test that cites a spec ID. The test can
  still misread the spec, so the builder's way out is a dispute.
- The forbidden-pattern check is a regex, the stub check a heuristic, and escalation keyword
  based. The inventory is the tests role's own declaration.
- The defaults target Kotlin/Gradle. Other stacks need their own gate.config, emitting JUnit XML
  and Pitest-format XML.
