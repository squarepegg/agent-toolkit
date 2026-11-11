# adversarial-sdlc

A skill for pi and other Agent Skills hosts (Claude Code works through `SDLC_RUNNER_<role>`, below). It produces code that has **survived adversarial
review** and states the residual risk. It does not claim "bug-free".

Every phase runs as a separate headless agent process with its own prompt and model. Each one
works in a fresh copy holding only its inputs, inside an OS sandbox (a Docker container, or macOS
`sandbox-exec`; `SDLC_SANDBOX=required` by default). The sandbox restricts writes to that copy and
gives every process its own Gradle home, pi agent dir and temp dir, so roles share no writable
state. On Docker a role sees only the directories mounted into its container. On `sandbox-exec`
reads under `$HOME` and temp are limited to an allowlist (toolchains, the read-only dependency
cache, this skill). One script, `scripts/gate.sh`, decides pass or fail, on a sandboxed snapshot. A model's
opinion never counts.

```text
requirements ─▶ spec ─▶ ⏸ human approval ─▶ tests (spec only; checked vs TODO() stubs) ─▶ ⏸ spec gaps?
     ─▶ [design ×N ─▶ judge] ─▶ build ─▶ attack (fresh, other model) ─▶ repro-validated findings ─▶ fix ─┐ ≤3
                                            ▲────────────────────────────────────────────────────────┘
     dry round ─▶ planted-bug calibration ─▶ report (verified / not verified / residual risk / escalations)
```

## Install

```sh
ln -s "$PWD" ~/.agents/skills/adversarial-sdlc        # pi and other Agent Skills hosts; run from this directory
ln -s "$PWD" ~/.claude/skills/adversarial-sdlc        # Claude Code
chmod +x scripts/*.sh scripts/*.py tests/*.sh tests/fakes/*.sh
```

Requirements: `pi`, Python 3.9+, an isolation backend (Docker, or macOS `sandbox-exec`; see
"Isolation backends"), and for the Kotlin defaults JDK 21 plus Gradle (needed once, to create the
wrapper; on Docker the images carry their own JDK). `ast-grep` (`npm i -g @ast-grep/cli`) is needed
for the consolidate step and for part of the unit tests.

## Run it

Ask the agent: *"use adversarial-sdlc to build X"*, or run the steps yourself:

```sh
S=<this directory>/scripts/sdlc.sh
$S --project my-lib init --scaffold kotlin --package mylib   # or plain `init` for an existing project
$EDITOR my-lib/sdlc.config                                   # models per role (below)
$S --project my-lib spec requirements.md                     # exits 7: read .sdlc/spec.md
$S --project my-lib approve                                  # type YES (human)
$S --project my-lib run                                      # exits 7 again if the tests role reports spec gaps
cat my-lib/.sdlc/REPORT.md
```

| exit | meaning | you do |
|---|---|---|
| 7 (repair) | a test repair is staged | read `.sdlc/repair/review.md`; `accept-repair` |
| 0 | done (report status DONE or GATE PASSED — HUMAN REVIEW REQUIRED) | review the escalations |
| 7 | a human decision is pending: spec approval or spec gaps | `approve`, amend the spec, or `accept-gaps` |
| 6 | tests phase rejected (implementation written, or inventory does not match a real run) | read `.sdlc/tests-feedback.md`, rerun |
| 3 | builder dispute against a locked test | rule; `resolve-disputes`, `relock` |
| 4 | gate still failing after `SDLC_BUILD_MAX_ITERS`, or the mutation step hit its time limit | read the last gate report; for a time limit, make Pitest faster or raise the limit, `relock`, rerun (the rerun gates the current code before calling the builder) |
| 5 | an attack round did not complete; that is not a clean round | fix the cause (often a provider timeout), rerun |

`run` resumes. A completion record is reused only if:
- its files are unchanged;
- it was made under the current spec and by the same version of the skill's scripts;
- for attack rounds, the attacker configuration (model, thinking, runner, tools, prompt) is the same;
- for a dry round, the code fingerprint matches as well.

The gate reuses a passing report only if the code fingerprint, spec, lock and scripts all match,
and no newer gate attempt was interrupted. A new `spec` archives the previous run. One command
runs per project at a time (atomic mutex).

When the agent orchestrates, it relays your decisions with `--relayed "<your words>"`. The record
stores your words and the agent's session id, and the report says the decision was relayed.

When `spec`, `tests`, `build`, `attack`, `calibrate`, `loop` or `run` stops, you get a macOS
notification with the exit code (`SDLC_NOTIFY=off` disables it; `SDLC_NOTIFY_CMD` runs your own
command instead, with the exit code and message as `$1` and `$2`).

## Configure models per role

`sdlc.config` in the project is sourced over `templates/sdlc.config`:

```sh
SDLC_MODEL_spec="provider/strong-model"
SDLC_MODEL_tests="provider/strong-model"
SDLC_MODEL_build="provider/mid-model"
SDLC_MODEL_attack="other-provider/other-model"     # another family than the builder
SDLC_MODEL_plant="provider/mid-model"              # writes the planted bugs for calibration
SDLC_THINKING_attack="high"
SDLC_PI_EXTENSIONS="$HOME/.pi/agent/git/example/pi-my-gateway"  # provider extension, if your provider needs one
```


- Any name from `pi --list-models` works (`provider/model`). Empty means pi's default.
- These can also be set per role: `SDLC_THINKING_<role>`, `SDLC_TOOLS_<role>` (a pi `--tools`
  allowlist) and `SDLC_SANDBOX_<role>`.
- `SDLC_RUNNER_<role>` replaces pi with another CLI (codex, gemini, claude). It runs in the role's
  workspace and gets the prompt and task on stdin; setting it is the authorisation. CLIs that
  sandbox themselves may need `SDLC_SANDBOX_<role>=off`, and the report then lists that role as
  unsandboxed.
- Roles run `pi -p -ne -ns -np -nc --no-mcp -na`. `roles.jsonl` records every role run: model,
  tools, extensions, extra args, prompt and task hashes, workspace fingerprint, sandbox and exit
  code.
- **Provider timeouts.** Behind a proxy that sends headers only once the model answers, a turn
  that thinks for more than 300 s fails with `fetch failed`. Lower the thinking level or pick a
  faster model.

## The gate

`gate.sh [--project DIR] [--label L]` copies the project to a snapshot and runs each step in
`GATE_STEPS` there, sandboxed (its report records the backend and, on Docker, the image IDs): it can write only to the snapshot and cannot read the project. It
writes `.sdlc/gate/<ts>-<label>-<pid>/gate-report.{md,json}`. It fails closed:

- every configured step must produce a result;
- `integrity`, `typecheck`, `test`, `mutation` and `static` are mandatory and must be `required`;
- a mode other than `required|optional|off` fails;
- inputs that change during the run fail it.

| step | Kotlin default | pass condition |
|---|---|---|
| integrity | built in | everything except main paths and docs matches `tests.lock` (tests, build files, wrapper, configs); no source-level suppressions |
| typecheck | `gradlew compileKotlin compileTestKotlin` (warnings are errors) | exit 0, then on the **compiled classes**: every class is inside `GATE_MAIN_PACKAGES`; no forbidden API (file, process, reflection, network, exit, env); mutation targets equal the owned packages; no hand-written method is excluded from mutation |
| test | `gradlew test --rerun` | ≥1 fresh case, 0 failed, 0 skipped, and **every inventory case ran and passed** |
| mutation | `gradlew pitest --rerun` | fresh report; KILLED/total ≥ `GATE_MUTATION_MIN` (80, unrounded); 0 run errors; no hand-written method hidden by the exclusions |
| static | `gradlew detekt --rerun` | exit 0, a fresh report with ≤ `GATE_STATIC_MAX_ISSUES` (0), and every main source is in a language it analyses (`GATE_STATIC_LANGS`) |
| fuzz | `gradlew test --rerun -PpropIterations=20000` | optional, off by default |

For other stacks, replace the commands. Tests must emit JUnit XML, mutation must emit
Pitest-format XML, and `GATE_SKELETON_PATHS` / `GATE_MAIN_PATHS` must describe the layout.
Reports are written by code under test, so a report with a DTD or entity declaration fails its
step, and so does a truncated one ("report unreadable").

**Time limits.** Each step has `GATE_STEP_TIMEOUT` seconds (1800), or `GATE_<STEP>_TIMEOUT`. A step
killed at its limit is reported as timed out (`timed_out` in the JSON); its partial reports are
not read. When the mutation step times out, the build loop stops with exit 4 instead of calling
the builder again: the builder cannot see or fix a time limit.

**Keeping mutation testing fast.** Pitest runs the covering tests once per mutant, so slow tests
dominate. Knobs in `gradle.properties` (locked; change them with `relock`):

| knob | default | effect |
|---|---|---|
| `sdlc.pitestExcludedTests` | empty | test classes Pitest leaves out (e.g. 1 MB capacity tests); the test step still runs them. Can only lower the score |
| `sdlc.pitestJvmArgs` | empty | minion JVM args; `-Dkotest.proptest.default.iteration.count=100` lowers property iterations under Pitest only |
| `sdlc.pitestThreads` | 2 | parallel minions; also raise `SDLC_DOCKER_CPUS`, and keep threads × minion heap under `SDLC_DOCKER_MEMORY` |
| `sdlc.pitestTimeoutMs` | 10000 | per-mutant timeout constant (short timeouts count slow passing runs as kills) |
| `sdlc.pitestHistory` | true | incremental analysis across gate runs, see below |

Build-loop gates pass `--mutation-history .sdlc/pitest-history` (`SDLC_MUTATION_HISTORY=1`):
Pitest reuses the results of an earlier gate for unchanged code. A gate that reused history is
marked `mutation_incremental` and is never final evidence: when it passes, the orchestrator
confirms with a fresh run (`--fresh`), and the report accepts only a non-incremental gate. The
gate deletes any history file left by earlier steps, so code under test cannot plant one.

The builder checks itself with `gate.sh --scratch --fast` (every step except mutation and fuzz)
and does not run Pitest; the real gate's surviving mutants come back to it in `last-gate/`.

## Isolation backends

`scripts/sandbox.sh` runs a command in a deny-by-default sandbox. It has two backends.

| `SDLC_SANDBOX_BACKEND` | meaning |
|---|---|
| `auto` (default) | `docker` if it is usable (daemon up, images built, cache volume present), else `sandbox-exec` |
| `docker` | one container per role and per code-under-test run; if Docker is unusable, no fallback to `sandbox-exec`: exit 97 under `required` |
| `sandbox-exec` | macOS only |

`SDLC_SANDBOX` is unchanged: `required` (default; exit 97 if no sandbox is available), `auto`
(warn and run unsandboxed; the report lists those roles) and `off`. `sandbox.sh --probe` prints the
backend that would be used: `docker`, `sandbox-exec`, `inherited` (already inside a sandbox) or
`none`. That value is recorded per role in `roles.jsonl` and in every gate report. On Docker, the
pinned image IDs are recorded next to it.

### Docker: setup

```sh
skills/adversarial-sdlc/scripts/docker/build.sh    # once, and after any change to docker/
```

- It builds `sdlc-gate`, `sdlc-role` and `sdlc-egress`, and writes their image IDs to
  `~/.cache/sdlc-docker/images.env` (`SDLC_DOCKER_STATE` moves it). Then it warms the
  `sdlc-gradle-ro` volume from the Kotlin template. `--no-warmup` skips that. `--platform LIST`
  also proves that a cross-platform build works.
- The image IDs are part of `harness_sha`. **Rebuilding changes them and invalidates harness-bound
  records**, the same way a script change does. This is by design.
- The Gradle module cache lives in the `sdlc-gradle-ro` volume. Containers mount it read-only.
  `scripts/docker/warmup.sh SKELETON_DIR [CMD]` fills it from a skeleton of locked build files; no
  role-written code runs there. `sdlc.sh` does this itself around the `tests` phase (and for
  test-only dependencies the tests role declares). Gate steps and code under test are offline, so a
  missing dependency fails the step instead of downloading.

### Docker: knobs

| variable | default | meaning |
|---|---|---|
| `SDLC_DOCKER_MEMORY` | `4g` | memory limit per container (swap is the same value) |
| `SDLC_DOCKER_CPUS` | `4` | CPU limit per container |
| `SDLC_DOCKER_PIDS` | `4096` | `--pids-limit` per container |
| `SDLC_DOCKER_IMAGE_GATE`, `_ROLE`, `_EGRESS` | from `images.env` | image reference to use instead; resolved to its ID, which is what gets pinned and recorded |
| `SDLC_DOCKER_PASSENV` | empty | extra host variable names to pass into containers (the rest of the environment is not inherited) |
| `SDLC_EGRESS_ALLOW` | the upstream | comma-separated `host:port` pairs the proxy may forward `CONNECT` and absolute-URI requests to |
| `SDLC_EGRESS_UPSTREAM` | gateway `host:port` | target of origin-form requests (the gateway); a localhost gateway URL becomes `host.docker.internal` |
| `SDLC_DOCKER_HOST_ALIAS` | `host.docker.internal` | name a localhost gateway is reached by from the proxy |
| `SDLC_DOCKER_STATE` | `~/.cache/sdlc-docker` | where `images.env` lives |
| `SDLC_DOCKER_VOLUME` | `sdlc-gradle-ro` | the read-only Gradle cache volume; `warmup.sh` fills it and `run.py` mounts it |

Model-role containers also get the caller's `SDLC_*` variables (not `SDLC_PROJECT`, `SDLC_NOTIFY_CMD` or
names that look like credentials); code under test gets only what `SDLC_DOCKER_PASSENV` names. The
model gateway is `SDLC_GATEWAY_URL` (`http://host:port`, default `http://localhost:8080`). Inside role
containers that variable is set to the egress proxy's address, and also under each name in
`SDLC_GATEWAY_ENV` (space-separated) for provider extensions that read the URL from another variable.
Providers that need an API key directly are not supported by the egress proxy: use a gateway that holds
the key.

### Docker: threat model

- **Adversaries:** every role process and all code under test (builder code, tests-role tests,
  attacker repros).
- **Trusted:** the human, the orchestrator (`sdlc.sh`, `sdlc_tool.py`, `gate.sh` on the host),
  Docker, the images, and the model gateway.
- **Out of scope:** a malicious operator on the laptop (that would need CI as the authority), and kernel or VM escapes (`runc` only).

### Docker: what a container gets

- **Files:** only the mounts listed for the call, at the same paths as on the host. Plus the skill
  directory read-only, `SDLC_SANDBOX_READ` read-only and, for model roles, `SDLC_PI_EXTENSIONS`
  read-only. Mounting `$HOME`, an ancestor of it, or a system root is refused. A `--deny` dir
  inside a mounted dir is refused. The directories leading to a mount exist but are empty.
- **Hardening:** read-only root filesystem, tmpfs home and temp, all capabilities dropped,
  `no-new-privileges`, the host UID, memory, CPU and pid limits, `--rm`. No Docker socket, never
  privileged.
- **Credentials:** none. No `auth.json`, no SSH agent, no host keychain, and an environment
  allowlist. The gateway must need no key from the container.
- **Network:** `--net local` and `none` mean no network (container `--network none`). `--net all`
  means an internal Docker network whose only exit is the egress proxy, which forwards to the
  gateway and refuses everything else (logged on stderr as `REFUSED`). On Docker, model roles
  always get `all`, whatever `SDLC_SANDBOX_NET_ROLES` says, because `local` would cut them off from
  the host gateway; the effective value is `sandbox_net` in `roles.jsonl`.
- **Nesting:** a call from inside a container (the builder's scratch gate) runs directly, detected
  by a marker file in the read-only image.

### Docker: operational notes

- **Shared paths.** Docker Desktop shares `/Users`, `/Volumes`, `/private`, `/tmp` and
  `/var/folders` by default. Colima shares only `~`: set `TMPDIR` to a directory under `~`, or add
  mounts in the Colima config. A runtime silently replaces an unshared host path with an empty
  directory, so `sandbox.sh` checks every mount from inside the container first. A path that is
  not shared stops the run with exit 97 ("mount not shared with the Docker VM").
- **Memory.** Gradle, Pitest and pi together are heavy: give the Docker VM at least 10 GB.
  Containers run one at a time.
- **Licensing.** Docker Desktop needs a paid subscription above 250 employees or $10M revenue;
  Colima and OrbStack use the same `docker` CLI.
- **Status.** Developed on Docker Desktop (macOS, arm64). Colima is not verified. Linux should work
  in principle and is untested.

### Docker: residual risks

The report prints the risk lines for the backend each role and the gate actually used. On Docker:

- Containers share the Docker VM's kernel (`runc`). Kernel and VM escapes are out of scope.
- Model roles reach the gateway. They can send anything they can read, their own workspace, to the
  model provider through it. The gateway and the images are trusted, not checked.
- Nothing stops the operator from swapping an image or editing `images.env`. A changed image shows
  up only as a different `harness_sha`.

### sandbox-exec backend

Selected with `SDLC_SANDBOX_BACKEND=sandbox-exec`, or by `auto` when Docker is not usable.

- **Writes:** only the declared directories.
- **Reads:** under `$HOME`, `/private/tmp` and the per-user temp dir, only these are readable:
  pi's install, `~/Library/Java`, `~/.sdkman`, `~/.local`, the Gradle wrapper and module cache,
  this skill, and `SDLC_SANDBOX_READ`. The real project, agent transcripts and other workspaces
  are always denied.
- **Private state:** every process gets its own Gradle home under `.sdlc-sbx/`, so init scripts
  and cache poisoning cannot persist. The dependency cache comes read-only from `~/.gradle/caches`
  and only the orchestrator's warm-up fills it, from the locked build files. Each process also
  gets its own pi agent dir (a copy of `auth.json` and minimal settings) and its own temp dir.
- **Sandbox check:** a nested call skips the sandbox only if a write probe proves it is already
  confined. The environment alone is not trusted.
- **Builds:** no Gradle daemon, an in-process Kotlin compiler, and IPv4 loopback for Gradle's own
  workers.
- **Network:** code under test (gate steps, repros, stub runs) can reach only loopback.
  Model roles use `SDLC_SANDBOX_NET_ROLES`: `all` by default, `local` for a gateway on localhost.
- **Read-only inputs:** while the gate runs, sources, tests, build files and configs are read-only.
  Only build output is writable. Each gate step gets fresh private state, so nothing it plants
  survives into the next step.
- **Imports:** everything the orchestrator copies out of a role workspace goes through a no-follow
  import. Symlinks and non-regular files are refused and logged.

`sandbox-exec` does not limit the network of model roles, or CPU and memory. Each role can read the
copy of `auth.json` in its own workspace.

Its residual risks are listed in the report: model roles keep the network they were given
(`SDLC_SANDBOX_NET_ROLES`), there are no CPU or memory limits, and a daemon started outside the
sandbox could be reused by a build that re-enables daemons.

## Outputs (`<project>/.sdlc/`)

| file | what |
|---|---|
| `spec.md`, `spec.approved` | spec, and approval bound to its sha256 |
| `inventory-check.json`, `spec-gaps.md`, `spec-gaps.accepted` | the tests checked against stubs; gaps; the human's acceptance |
| `tests.lock` | sha256 of everything locked, plus the spec it was written for |
| `gate/*/gate-report.md` | the only evidence of a pass (its json records the md's hash, spec, lock and code fingerprint) |
| `attack/round-N/findings.validated.json`, `attacker.done` | each finding: reproduced / discarded (why) / flaky / spec-gap; completion record |
| `calibration/*/calibration.json` | planted bugs caught k/n, bound to the code and attacker configuration |
| `escalations.md`, `REPORT.md` | regenerated on every report |
| `roles.jsonl`, `logs/`, `sessions/`, `violations.log` | audit trail |

## Optional: code under test on Kubernetes

`SDLC_SANDBOX_BACKEND_CODE=k8s` runs gate steps, repros and mutant checks in pods instead of local
containers (`scripts/k8s/run.py`, image from `scripts/k8s/build.sh`). Model roles always stay on
Docker. It never falls back: an unusable cluster fails closed (exit 97). The cluster side (a
`sdlc-runner` namespace with no egress, a `sdlc-runner-pods` ValidatingAdmissionPolicy, a node pool
labelled `category=sdlc` with a matching taint, an image pull secret) is not part of this repo.
Settings are `SDLC_K8S_*` (context, namespace, registry, image name, node pool, pull secret, CPU and
memory); read the head of `scripts/k8s/run.py` and `scripts/k8s/build.sh`. Production-looking contexts
are refused. Guarantees and non-guarantees: `docs/specs/k8s-runner-threat-model.md`. Docker design and
acceptance criteria: `docs/specs/docker-backend.md`.

## Tests

```sh
python3 tests/<name>_unit_test.py      # fast unit tests, no Docker and no model calls
tests/selftest.sh [--quick]            # about 25 minutes, see below
```

Some unit tests (rule validation, consolidation, GATE_RULES) need `ast-grep` on `PATH`.

`tests/selftest.sh` makes no model calls. Fake roles drive the real orchestrator, sandbox and Gradle
on a tiny fixture. It checks that:

- an unapproved spec blocks;
- a tests role that writes an implementation is rejected;
- a malicious builder cannot touch the project, the lock, the transcripts, the tests or gradlew;
- malformed findings fail the round;
- a dry round triggers calibration;
- changes to code, spec or gate report invalidate DONE;
- the gate fails closed on a crashed evaluator, a misspelled mode, or a mandatory step made optional;
- lock holes (e.g. `src/test/kotlin/out/`) are caught;
- `REPRO_RUNS < 2` and `MAX_ROUNDS = 0` are refused.

`--quick` skips the orchestration part. `SELFTEST_CODE_BACKEND=k8s` runs the orchestration part's code
under test on a dev cluster (needs `SDLC_K8S_CONTEXT`).

## Lineage

The attacker is the `doubt-driven-development` cycle made mechanical. The verification rules come from
Phoenix-style regeneration skills (implementation-memory audit, eval tiering, deletion test). See [`reference/lineage.md`](reference/lineage.md).
