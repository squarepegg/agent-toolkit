# Spec: Docker isolation backend for adversarial-sdlc

- Status: **done**. Every AC passes (see Verification below). ESC-2 was accepted without a separate
  line-by-line review of the proxy and env allowlists; do that review before relying on the backend
  for anything sensitive. Approved 2026-11-01, with amendments A-1 to A-4 below.
- Decision (2026-10-31): use **Docker**, building against the plain `docker` CLI/socket so that
  Colima, OrbStack and Linux CI also work. Docker Desktop 29.8.1 runs on this laptop: linux/arm64
  VM, 16 CPUs, about 7.7 GB of memory, `runc` only. Docker Desktop requires a subscription at more
  than 250 employees or $10M revenue; check that before relying on it.

## Purpose

Replace the macOS `sandbox-exec` backend with one container per role and per code-under-test run.
A role must not reach anything except the files mounted into its container and the model gateway.
The orchestrator stays on the host, roles stay adversarial, and the human and orchestrator are
trusted (threat model below). `sandbox-exec` remains as a fallback backend.

## Threat model

- **Adversaries:** every role process (spec, tests, design, judge, build, attack, referee, plant),
  and all code under test (builder-written main code, tests-role tests, attacker repros).
- **Trusted:** the human, the orchestrator (`sdlc.sh`, `sdlc_tool.py`, `gate.sh` on the host),
  Docker itself, the images the orchestrator builds, and the model gateway.
- **Out of scope:** a malicious operator on the laptop. Only CI-as-authority addresses that
  (it would need CI as the authority). Kernel or VM escapes are also out of scope: `runc` only on Docker Desktop;
  gVisor is a CI/Linux option.

## Interface

The backend keeps the existing `sandbox.sh` contract, so callers do not change.

```text
sandbox.sh [--rw DIR]... [--ro DIR]... [--deny DIR]... [--net all|local|none] -- COMMAND [ARGS]
sandbox.sh --probe                   # prints: docker | sandbox-exec | inherited | none
SDLC_SANDBOX=required|auto|off       # unchanged meaning
SDLC_SANDBOX_BACKEND=docker|sandbox-exec|auto   # new; auto = docker if `docker info` works, else sandbox-exec
SDLC_DOCKER_IMAGE_ROLE, _GATE, _EGRESS          # image refs (default: locally built; resolved to and run by image ID)
SDLC_DOCKER_MEMORY, SDLC_DOCKER_CPUS, SDLC_DOCKER_PIDS  # per-container limits (defaults: 4g, 4, 4096)
SDLC_DOCKER_PASSENV, SDLC_DOCKER_STATE, SDLC_DOCKER_HOST_ALIAS, SDLC_EGRESS_ALLOW, SDLC_EGRESS_UPSTREAM
                                                # added while building; see README "Isolation backends"
# The gateway URL must be http://host:port on the Docker backend. Images are pinned by local image
# ID (sha256 of the image config), recorded per Docker context, not by registry digest.
```

Callers today, which must keep working unchanged:

| caller | use |
|---|---|
| `run-role.sh:36` | each role: `--rw WS --rw SESS --deny PROJECT --net $SDLC_SANDBOX_NET_ROLES` |
| `gate.sh:90` | each gate step: `--ro ROOT --rw ROOT/<generated>… --net local --deny PROJECT` (scratch: `--rw ROOT --net local`) |
| `sdlc.sh:183` (`sbx_code`) | the tests role's stub run: `--ro DIR --rw DIR/<generated>… --net local` |
| `sdlc_tool.py:321` (`sandboxed`) | repro validation, mutant checks and calibration scoring in disposable copies |

Mapping onto Docker:

| flag | Docker |
|---|---|
| `--ro DIR` | `--mount type=bind,src=DIR,dst=DIR,readonly` (same path inside, so tool output paths stay valid) |
| `--rw DIR` | `--mount type=bind,src=DIR,dst=DIR` |
| `--deny DIR` | not mounted. Nothing is mounted unless listed, so deny is the default; the flag is accepted and checked: a denied dir must not be under any mounted dir |
| `--net none` / `local` | `--network none`. Code under test needs only in-container loopback |
| `--net all` | an internal network whose only exit is the egress proxy (see INV-4) |
| cwd | `--workdir` = the caller's cwd, which must be inside a mounted dir |
| env | an explicit allowlist: the fixed container variables (`HOME`, `TMPDIR`, `GRADLE_USER_HOME`, `GRADLE_RO_DEP_CACHE`, `JAVA_TOOL_OPTIONS`, `PI_CODING_AGENT_DIR`, `GATE_JAVA_HOME`), plus `SDLC_DOCKER_PASSENV`. Model-role containers (`--net all`) also get the host's `SDLC_*` (minus `SDLC_PROJECT`, `SDLC_NOTIFY_CMD` and names ending `_TOKEN`, `_KEY`, `_SECRET`, `_PASSWORD`, `_CREDENTIAL(S)`; no `GRADLE_*`: a role can send what it reads to the model provider; 2026-11-07) and `SDLC_GATEWAY_URL` (set to the egress proxy's address; also under each name in `SDLC_GATEWAY_ENV`); code-under-test containers (`--net local\|none`) do not, since credentials such as `GRADLE_ENTERPRISE_ACCESS_KEY` live there (2026-11-05). Nothing else is inherited |
| stdin/stdout | `-i`, with output piped to the caller (keeps the existing `… 2>&1 \| cat > LOG` pattern) |
| exit code | the container's exit code. `docker` errors (125/126/127) map to 97 "sandbox unavailable" |

Images, built by `scripts/docker/build.sh` from `scripts/docker/*.Dockerfile`, linux/arm64 and amd64:

- `sdlc-role`: Debian slim, Node 26, pi 1.4.0 pinned, the gateway extension copied in, JDK 21,
  Gradle 8.10 wrapper distribution, Python 3, bash, coreutils. It runs as a non-root UID.
- `sdlc-gate`: `sdlc-role` minus pi and Node (code under test needs no agent).
- `sdlc-egress`: a minimal HTTP CONNECT/forward proxy that allows only
  `host.docker.internal:8080` (the gateway; configurable via `SDLC_EGRESS_ALLOW`).

Volumes:

- `sdlc-gradle-ro`: the Gradle module cache. A `warmup` container writes it from the locked
  skeleton only; every other container mounts it read-only as `GRADLE_RO_DEP_CACHE`.
- Per-run private state (Gradle home, pi agent dir, TMPDIR) is a container tmpfs, discarded on exit.

## Invariants

- INV-1: A container sees only the paths passed as `--ro`/`--rw`, the image, the read-only Gradle
  cache volume and tmpfs. No host path under `/Users`, `/Volumes` or `/private` is visible unless
  explicitly mounted.
- INV-2: Writes from inside a container land only in `--rw` mounts and tmpfs. A `--ro` mount
  and the Gradle cache volume are unwritable from inside.
- INV-3: `--net none`/`local` containers have no network interface except loopback.
- INV-4: `--net all` containers can open TCP connections only to the egress proxy. The proxy
  forwards only to the allowlisted gateway host and port. Every other destination is refused and
  logged.
- INV-5: No container receives credentials. There is no `auth.json`, no host environment beyond
  the allowlist, no SSH agent, no Docker socket and no host keychain. The model gateway needs
  none (the extension sends a placeholder key). A provider that needs a key is injected only
  through the proxy (Open questions).
- INV-6: Every container runs `--rm`, `--read-only` root filesystem (tmpfs for /tmp and home),
  `--cap-drop ALL`, `--security-opt no-new-privileges`, a non-root UID, `--pids-limit`, memory
  and CPU limits. No process survives `sandbox.sh` returning: the container is removed, or killed
  and removed on timeout.
- INV-7: The Docker socket is never mounted into any container, and no container is privileged.
- INV-8: Images are pinned. Roles and gate steps run by image digest, and the digest is recorded
  in `roles.jsonl` and gate reports. It is part of `harness_sha` (or recorded next to it), so an
  image change invalidates harness-bound records the way a script change does.
- INV-9: The backend fails closed. If Docker is unavailable under `SDLC_SANDBOX=required`, the
  command does not run (exit 97). There is no silent fallback to `sandbox-exec` unless
  `SDLC_SANDBOX_BACKEND=auto`, and the fallback is recorded.
- INV-10: Behaviour visible to callers is unchanged: the same flags, exit codes, cwd semantics,
  stdout/stderr and file results in `--rw` dirs. All 93 self-test checks pass on the Docker
  backend, with the sandbox-mechanics checks replaced as in AC-6.
- INV-11: Files the container writes into `--rw` dirs are owned by the host user, or chowned back
  before the orchestrator reads them. The orchestrator's no-follow import rules still apply.

## Failure modes

| ID | Trigger | Required behaviour | Must never happen |
|---|---|---|---|
| FM-1 | Docker daemon down or not installed | `--probe` says `none` or `sandbox-exec`; `required` exits 97 with a clear message | running unsandboxed |
| FM-2 | Image missing or digest mismatch | build or pull it (warm-up), or fail with "image not built: run scripts/docker/build.sh" | running a different image than recorded |
| FM-3 | Role exceeds its memory or pid limit | the container is killed; exit code non-zero, mapped to the role's failure path | the host or VM freezing; other containers affected |
| FM-4 | Timeout (`run-timeout`) | `docker kill`, then `docker rm`; exit 124 | an orphaned container still running |
| FM-5 | Role creates a symlink in a `--rw` dir pointing at `/Users/...` | the host import refuses it (existing no-follow import) | the orchestrator following it |
| FM-6 | Role tries the Docker socket, host.docker.internal on other ports, the metadata IP or the LAN | connection refused (no route, or proxy refuses) and logged | any connection succeeding |
| FM-7 | Gateway unreachable from the proxy | role fails, and the existing retry with backoff applies | silently using another provider |
| FM-8 | UID mismatch makes output files unreadable or unwritable on the host | chown fix-up in `sandbox.sh`, or run with the host UID mapped | the orchestrator crashing or skipping imports |
| FM-9 | Gradle cache volume empty (first run) | the warm-up container fills it from the locked skeleton; role builds may download inside their own tmpfs if the network allows; gate steps (`--net none`) fail clearly if a dependency is missing | a gate step reaching the internet |
| FM-10 | Docker Desktop VM out of memory (Gradle + Pitest + pi) | one container at a time (current sequential design); a documented minimum VM memory (10 GB recommended) | parallel containers starving each other |
| FM-11 | Nested call (builder's scratch gate inside its role container) | runs directly inside the role container (already confined); detected by a marker file the image provides and roles cannot create (read-only rootfs), not by an env var | starting Docker-in-Docker, or mounting the socket |
| FM-12 | Path with spaces or unicode in a mount | handled (mount syntax quoted) | mount injection |

## Acceptance criteria

- AC-1: `SDLC_SANDBOX_BACKEND=docker tests/selftest.sh` passes, with the sandbox section rewritten per AC-6.
- AC-2: `sdlc.sh run` on a sample project completes on the Docker backend with the same status class as the `sandbox-exec` run (GATE PASSED — HUMAN REVIEW REQUIRED).
- AC-3: Inside a role container, `ls /Users`, `cat ~/.pi/agent/auth.json`, `ls /var/run/docker.sock` and `curl https://example.com` all fail, and `curl $SDLC_GATEWAY_URL/v1/models` succeeds through the proxy.
- AC-4: Inside a gate container, writing to a `--ro` path fails, writing to `build/` succeeds, and any network call except loopback fails.
- AC-5: After a run, `docker ps -a --filter label=sdlc` is empty, and no `sdlc-*` temp dirs are left on the host.
- AC-6: Self-test sandbox section on Docker:
  - the workspace has no `/Users`;
  - denied paths are absent;
  - the root filesystem is read-only;
  - there is no Docker socket;
  - the proxy refuses a non-allowlisted host;
  - a spoofed nested marker in a `--rw` dir is ignored.
- AC-7: The same self-test passes on Colima, to prove Docker-Desktop independence.
- AC-8: The image digest appears in `roles.jsonl` and in gate reports, and changing the image invalidates harness-bound records (a self-test checks this).
- AC-9: The README and SKILL.md document the backend and its threat model, and the residual-risk lines in the report reflect the backend actually used (per run, from `roles.jsonl`).

## Verification (2026-11-01)

| AC | evidence |
|---|---|
| AC-1 | `SDLC_SANDBOX_BACKEND=docker tests/selftest.sh`: 112/112 on Docker Desktop. `sandbox-exec` still passes 93/93 |
| AC-2 | full run of a sample project on Docker: GATE PASSED — HUMAN REVIEW REQUIRED, 66 m 42 s, calibration 5/5 |
| AC-3 | self-test: no host files under `/Users` beyond the mounts (A-7), no host `auth.json`, no Docker socket, `example.com` refused, gateway reachable through the proxy |
| AC-4 | self-test: `--ro` input unmodified, `build/` writable, `--net local` reaches neither the internet nor the host gateway |
| AC-5 | self-test: no `label=sdlc` container or network after a SIGKILLed run. Same after a full run |
| AC-6 | self-test "docker backend" section: no `/Users`, denied paths absent, read-only rootfs (`/work` write), no socket, proxy refuses and logs, forged marker rejected |
| AC-7 | `DOCKER_CONTEXT=colima TMPDIR=~/.cache/sdlc-tmp tests/selftest.sh`: 112/112 on Colima 0.10.3 (vz, virtiofs, default mounts) |
| AC-8 | self-test: image IDs in `roles.jsonl` (`sandbox_images`) and gate reports (`sandbox.images`). A different image ID changes `harness_sha` |
| AC-9 | README.md and SKILL.md "Backends" sections. `REPORT.md` prints a residual-risk line for each backend found in `roles.jsonl` and the gate report |

Found while building and fixed in the harness:
- the file digest used `access(X_OK)`, which Docker Desktop bind mounts report as true for every
  file, so every locked file looked changed (it now uses the owner-exec bit);
- `shasum` does not exist on Debian (portable `sha256`);
- `gate.config` found the JDK only through macOS `java_home` (containers set `GATE_JAVA_HOME`);
- the tests role's stub run is offline on Docker, so test dependencies are warmed first.

Open questions resolved:
- bind mounts are not slower (measured), so they stay;
- Colima works with `TMPDIR` under `~`, and an unshared path fails closed with exit 97;
- Docker Desktop licensing depends on your organisation; Colima and OrbStack use the same CLI.

## Amendments (approved 2026-11-01)

- A-1: Skill dir mount. Every container also gets the skill dir (`skills/adversarial-sdlc`)
  mounted read-only at the same path. Containers run `sdlc_tool.py run-timeout …`, and the
  builder's in-container `gate.sh --scratch` needs the scripts. INV-1's visible set includes it.
  The skill dir is never writable and is already part of `harness_sha`.
- A-2: Gradle wrapper. The wrapper writes `<dist>.lck` next to its distribution, so the copy
  baked into the image cannot be used read-only. The image ships it under `/opt/gradle-dists`,
  and each container copies the pinned distribution into its tmpfs `GRADLE_USER_HOME` at start.
- A-3: Mount verification. Docker runtimes silently replace an unshared host path with an empty
  VM directory (Colima shares only `~` by default). Before running COMMAND, `sandbox.sh` checks
  every mount from inside the container. Each `--rw` dir gets a host-written nonce file that
  must be visible inside, and is then removed. Each non-empty `--ro` dir must show the same
  top-level entries as on the host. A mismatch exits 97 ("mount not shared with the Docker VM")
  and the command does not run.
- A-4: Egress proxy design. `sdlc-egress` is a stdlib Python proxy.
  - Origin-form requests (`GET /v1/models`) are reverse-proxied to the single upstream
    `SDLC_EGRESS_UPSTREAM` (default `host.docker.internal:8080`). This is what
    `SDLC_GATEWAY_URL=http://sdlc-egress:8080` uses.
  - `CONNECT` and absolute-URI requests are allowed only to `host:port` pairs in
    `SDLC_EGRESS_ALLOW` (default: the upstream). Anything else gets 403 and a log line on stderr.
  - The proxy is the only container on both the internal network and the default bridge.
    Role containers sit on the internal network only (`docker network create --internal`).
  - Each `--net all` call gets its own network and proxy container, removed with the role
    container. Only REFUSED lines are copied to the caller's stderr.
- A-5 (found while building): Role network. On Docker, `--net all` already means "gateway
  only". `--net local` would cut a role off from the gateway, which runs on the host. So
  `run-role.sh` always passes `all` on Docker, whatever `SDLC_SANDBOX_NET_ROLES` says, and
  records the effective value as `sandbox_net` in `roles.jsonl`. Code under test still gets
  `--network none`.
- A-6 (found while building): Read paths. `SDLC_SANDBOX_READ` entries are mounted read-only for
  every container, and `SDLC_PI_EXTENSIONS` entries for model roles (`--net all`), at the same
  paths. The gateway extension is not baked into the image, so `-e <host path>` keeps working.
  Mounting `$HOME`, an ancestor of it, or a system root (`/`, `/Users`, `/Volumes`,
  `/private`, `/home`) is refused (exit 2).
- A-7 (found while building): Path skeleton. Mounts keep the host path, so the directories leading
  to a mount (e.g. `/Users/<you>/projects/...`) exist inside the container, but are empty
  except for that one path. AC-3's `ls /Users` and AC-6's "no `/Users`" are therefore checked
  as "`/Users` holds only the path components leading to mounted dirs", not as "`/Users` does
  not exist".
- A-8 (found while building): Runner. The host-side backend is `scripts/docker/run.py` (Python:
  quoting, JSON and signals are where the shell glue broke before). `sandbox.sh` keeps
  argument parsing and backend choice, then execs it. `run.py` writes `docker create`, then
  `docker start -ai`, then reads `docker inspect` State, so a container that never starts
  (exit 97) is distinguished from a command that exits 125/126/127. A detached reaper removes
  the containers if `run.py` is SIGKILLed.

## Non-goals

- NG-1: CI-as-authority (signed gate reports from ephemeral runners). That is future work, and these images are designed to be reused for it.
- NG-2: gVisor or Kata isolation on the laptop.
- NG-3: Running the orchestrator itself in a container.
- NG-4: Parallel role execution.
- NG-5: Windows support.

## Open questions

- Is Docker Desktop licensed for your organisation? proposed: assume yes for building; verify on Colima per AC-7.
- Providers that need API keys (Anthropic, OpenAI direct): proposed: the proxy injects the key header, so containers never hold it. Out of scope for the first cut, which uses one gateway only.
- Should Codex/Gemini custom runners (`SDLC_RUNNER_<role>`) run inside the role image? proposed: yes for pi; custom CLIs need their own image layer, so document it and defer.
- Bind mounts versus copying into named volumes for speed: proposed: bind mounts first (simpler, same paths); measure, and move workspaces to volumes only if Gradle I/O is more than 30% slower than the `sandbox-exec` baseline.
- Should a role's own `gradlew test` inside its workspace keep `--net local`? proposed: yes; downloads come from the read-only cache.

## Escalation triggers

- ESC-1: concurrency: does-not-touch: sequential by design (NG-4).
- ESC-2: auth: touches: credential handling moves (INV-5), and the egress proxy is a security boundary. A human must review the proxy allowlist and the env allowlist.
- ESC-3: data-migration: does-not-touch: run state formats are unchanged; image digests are an additive field.
- ESC-4: exactly-once: does-not-touch.

## Implementation notes

1. Start with `scripts/docker/` (Dockerfiles, `build.sh`, proxy) and a `docker` branch in
   `sandbox.sh` behind `SDLC_SANDBOX_BACKEND`. Keep `sandbox-exec` working.
2. Lessons from `sandbox-exec` that still apply:
   - Java needs `-Djava.net.preferIPv4Stack=true` only for the old SBPL loopback rules. Drop it in
     containers and re-test.
   - `org.gradle.daemon=false` and `kotlin.compiler.execution.strategy=in-process` stay.
   - Logs go through pipes, not file redirects into denied paths.
   - macOS `killpg` returns EPERM on zombie leaders. Container kill replaces the process-group logic
     for container runs, but keep it for host-side timeouts.
3. A provider extension that reads the gateway URL from its own variable gets it through `SDLC_GATEWAY_ENV`. Inside containers the URL is set to the
   proxy (`http://sdlc-egress:8080`, which forwards to `host.docker.internal:8080`). Its
   model-list fetch aborts after 1.5 s: expect "model not found" under load, which the attacker
   retry/backoff handles.
4. Measure before and after: one `gate.sh` run and one full sample run, for each backend.
