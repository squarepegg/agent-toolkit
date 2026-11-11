#!/usr/bin/env bash
# selftest.sh: exercise the skill's enforcement end to end, with no model calls.
#
# Fake roles (tests/fakes/*.sh) run through the real orchestrator as custom runners, inside the real
# sandbox, on a tiny Kotlin fixture (clamp). About 10-15 minutes, dominated by Gradle.
# Usage: tests/selftest.sh [--quick]   (--quick skips the orchestration part)
#   Default: everything runs on this machine (Docker or sandbox-exec; heavy: real Gradle and Pitest).
#   SELFTEST_CODE_BACKEND=k8s   the orchestration part's code under test (its gates, repros, mutant checks and the builder's
#   checks) runs on the k8s backend instead. Needs SDLC_K8S_CONTEXT (a dev- context; prod contexts are refused) and a
#   cluster set up for scripts/k8s/run.py (docs/specs/k8s-runner-threat-model.md). The Docker backend's own checks and the
#   roles stay on local Docker. If the cluster is unusable the run stops with one clear message.
#   AWS_PROFILE=... / SDLC_K8S_SERVER=...   passed through to kubectl and the backend when set.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
SKILL="$ROOT"
S="$SKILL/scripts/sdlc.sh"; G="$SKILL/scripts/gate.sh"
TOOL=(python3 "$SKILL/scripts/sdlc_tool.py")
T="$(mktemp -d "${TMPDIR:-/tmp}/selftest-XXXXXX")"; T="$(cd "$T" && pwd -P)"
P="$T/clamp"
[ "${SELFTEST_KEEP:-0}" = 1 ] && echo "selftest dir: $T" || trap 'rm -rf "$T"' EXIT
PASS=0; FAIL=0
CODE_K8S="${SELFTEST_CODE_BACKEND:-local}"; unset SDLC_SANDBOX_BACKEND_CODE  # only the orchestration part may use k8s
case "$CODE_K8S" in k8s|local) ;; *) echo "SELFTEST_CODE_BACKEND must be k8s or local (got '$CODE_K8S')" >&2; exit 2 ;; esac
if [ "$CODE_K8S" = k8s ]; then
  [ -n "${SDLC_K8S_CONTEXT:-}" ] || { echo "selftest: SELFTEST_CODE_BACKEND=k8s needs SDLC_K8S_CONTEXT (a dev- context)" >&2; exit 2; }
  export SDLC_K8S_CONTEXT
  case "$SDLC_K8S_CONTEXT" in
    dev-*) ;;
    *) echo "selftest: k8s context '$SDLC_K8S_CONTEXT' is not a dev context; the self-test never runs on prod" >&2; exit 2 ;;
  esac
  [ -n "${SDLC_K8S_SERVER:-}" ] || SDLC_K8S_SERVER="$(kubectl config view --raw --minify --context "$SDLC_K8S_CONTEXT" \
    -o jsonpath='{.clusters[0].cluster.server}' 2>/dev/null)"
  export SDLC_K8S_SERVER
fi
ok()  { echo "  ok   $*"; PASS=$((PASS + 1)); }
bad() { echo "  FAIL $*"; FAIL=$((FAIL + 1)); }
expect() { [ "$1" = "$2" ] && ok "$3" || bad "$3 (want $1, got $2)"; }
has() { grep -q -- "$2" "$1" 2>/dev/null && ok "$3" || bad "$3 (no '$2' in ${1#"$T"/})"; }
sedi() { sed -i '' "$@" 2>/dev/null || sed -i "$@"; }
sdlc() { "$S" --project "$P" "$@" > "$T/last.out" 2>&1; echo $?; }

# ---------------------------------------------------------------- fixture
mkdir -p "$P/src/main/kotlin" "$P/src/test/kotlin"
for f in build.gradle.kts settings.gradle.kts detekt.yml gradle.properties; do
  sed -e "s/__PACKAGE__/clamp/g" -e "s/__NAME__/clamp/g" "$SKILL/templates/kotlin/$f" > "$P/$f"
done
sed -e "s/__PACKAGE__/clamp/g" "$SKILL/templates/gate.config.kotlin" > "$P/gate.config"
cp -R "$SKILL/templates/kotlin/sdlcPitest" "$P/src/sdlcPitest"
cp -Rp "$ROOT/tests/fixture/gradlew" "$ROOT/tests/fixture/gradlew.bat" "$ROOT/tests/fixture/gradle" "$P/"
export FAKES="$ROOT/tests/fakes" FAKE_PROJECT="$P"
cat > "$P/sdlc.config" <<'EOF'
SDLC_RUNNER_spec="bash $FAKES/spec.sh"
SDLC_RUNNER_tests="bash $FAKES/tests.sh"
SDLC_RUNNER_build="bash $FAKES/build.sh"
SDLC_RUNNER_attack="bash $FAKES/attack.sh"
SDLC_RUNNER_plant="bash $FAKES/plant.sh"
SDLC_RUNNER_referee="bash $FAKES/referee.sh"
SDLC_REPRO_TIMEOUT=120
SDLC_MODEL_attack="fake-attacker"
SDLC_MODEL_build="fake-builder"
SDLC_BUILD_MAX_ITERS=1
SDLC_ATTACK_RETRIES=1
SDLC_SANDBOX=required
SDLC_SANDBOX_READ="$FAKES"
# Docker backend: the fakes read these; nothing else from the host environment reaches a container.
SDLC_DOCKER_PASSENV="FAKES FAKE_PROJECT FAKE_TESTS FAKE_BUILD FAKE_ATTACK FAKE_REFEREE"
EOF
# Notifications go to a file instead of the desktop (fix 7).
echo "SDLC_NOTIFY_CMD='echo \"\$1 \$2\" >> \"$T/notify.log\"'" >> "$P/sdlc.config"
printf 'clamp an Int into [lo, hi]\n' > "$T/req.md"

echo "sandbox"
SBX="$SKILL/scripts/sandbox.sh"
mkdir -p "$T/secret" "$T/sdlc-ws"; echo s > "$T/secret/f"
"$SBX" --rw "$T/sdlc-ws" --deny "$T/secret" -- bash -c "cat '$T/secret/f'" > /dev/null 2>&1 && bad "denied dir readable" || ok "denied dir is unreadable"
"$SBX" --rw "$T/sdlc-ws" --deny "$T/secret" -- bash -c "echo x > '$T/secret/g'" > /dev/null 2>&1 && bad "denied dir writable" || ok "denied dir is unwritable"
"$SBX" --rw "$T/sdlc-ws" -- bash -c "echo x > '$T/sdlc-ws/y'" > /dev/null 2>&1 && ok "own workspace is writable" || bad "own workspace writable"
"$SBX" --rw "$T/sdlc-ws" -- bash -c "ls '$HOME/.pi/agent/sessions'" > /dev/null 2>&1 && bad "agent transcripts readable" || ok "agent transcripts are unreadable"
"$SBX" --rw "$T/sdlc-ws" -- bash -c "mkdir -p '$HOME/.gradle/init.d' && touch '$HOME/.gradle/init.d/sdlc-selftest.gradle'" > /dev/null 2>&1 \
  && { rm -f "$HOME/.gradle/init.d/sdlc-selftest.gradle"; bad "shared Gradle init.d writable"; } || ok "shared ~/.gradle (init scripts, caches) is not writable (C1)"
"$SBX" --rw "$T/sdlc-ws" -- bash -c "touch '$HOME/.pi/agent/sdlc-selftest'" > /dev/null 2>&1 \
  && { rm -f "$HOME/.pi/agent/sdlc-selftest"; bad "shared pi agent dir writable"; } || ok "shared ~/.pi/agent is not writable"
mkdir -p "$T/other"; echo secret > "$T/other/impl.kt"
"$SBX" --rw "$T/sdlc-ws" -- bash -c "cat '$T/other/impl.kt'" > /dev/null 2>&1 && bad "other temp files readable" || ok "other temp files (e.g. old checkouts) are unreadable (H3)"
"$SBX" --rw "$T/sdlc-ws" -- bash -c "ls '$HOME/projects' '$HOME/Documents'" > /dev/null 2>&1 && bad "home readable" || ok "the rest of \$HOME is unreadable (H3)"
SDLC_IN_SANDBOX=1 "$SBX" --rw "$T/sdlc-ws" --deny "$T/secret" -- bash -c "cat '$T/secret/f'" > /dev/null 2>&1 \
  && bad "SDLC_IN_SANDBOX=1 bypassed the sandbox" || ok "a spoofed SDLC_IN_SANDBOX marker does not skip the sandbox (H3)"

mkdir -p "$T/sdlc-ro/src" "$T/sdlc-ro/build"; echo keep > "$T/sdlc-ro/src/A.kt"
"$SBX" --ro "$T/sdlc-ro" --rw "$T/sdlc-ro/build" --net local -- bash -c "echo x >> '$T/sdlc-ro/src/A.kt'" > /dev/null 2>&1
grep -qx keep "$T/sdlc-ro/src/A.kt" && ok "code under test cannot modify its read-only inputs (C3)" || bad "ro inputs modified"
"$SBX" --ro "$T/sdlc-ro" --rw "$T/sdlc-ro/build" --net local -- bash -c "echo x > '$T/sdlc-ro/build/out'" > /dev/null 2>&1 \
  && ok "code under test can write its build output" || bad "build output writable"
"$SBX" --rw "$T/sdlc-ws" --net local -- bash -c "curl -s -m 5 https://example.com" > /dev/null 2>&1 \
  && bad "network reachable with --net local" || ok "--net local blocks outbound network (C4)"
if [ "$("$SBX" --probe)" = docker ]; then
echo "docker backend (AC-4, AC-5, AC-6, AC-8)"
in_c() { "$SBX" --rw "$T/sdlc-ws" --deny "$T/secret" "$@" 2>&1; }
out="$(in_c -- bash -c "find /Users /Volumes -type f 2>/dev/null | grep -v -e '^$SKILL/' -e '^$T/sdlc-ws/' | head -3")"
[ -z "$out" ] && ok "no host files under /Users or /Volumes except the mounted dirs (INV-1, A-7)" || bad "host files visible: $out"
in_c -- bash -c "test ! -e '$T/secret'" > /dev/null && ok "denied paths are absent" || bad "denied path present"
# Host environment: code under test gets none of the host's SDLC_*/GRADLE_* (credentials live there); roles keep SDLC_*.
n="$(GRADLE_ENTERPRISE_ACCESS_KEY=selftest-secret SDLC_SELFTEST_SETTING=s in_c --net local -- \
      bash -c 'env | grep -c -e GRADLE_ENTERPRISE_ACCESS_KEY -e SDLC_SELFTEST_SETTING' | tail -1)"
[ "$n" = 0 ] && ok "code containers inherit no host GRADLE_*/SDLC_* variables" || bad "host variables reached code under test ($n)"
n="$(SDLC_SELFTEST_SETTING=s GRADLE_ENTERPRISE_ACCESS_KEY=x SDLC_SELFTEST_TOKEN=t in_c --net all -- \
      bash -c 'env | grep -c -e "^SDLC_SELFTEST_SETTING=" -e "^GRADLE_ENTERPRISE_ACCESS_KEY=" -e "^SDLC_SELFTEST_TOKEN="' | tail -1)"
[ "$n" = 1 ] && ok "model-role containers get the harness's SDLC_* settings, no GRADLE_* and no credential-like names" \
  || bad "role container env ($n of 1)"
# /work is world-writable in the image, so only --read-only stops this write (non-root cannot write /etc anyway).
in_c -- bash -c 'touch /work/sdlc-x' > /dev/null && bad "rootfs writable" || ok "the root filesystem is read-only"
in_c -- bash -c 'echo sdlc-docker-backend > /etc/sdlc-container' > /dev/null && bad "marker writable" || ok "the nested-call marker cannot be forged inside a container (FM-11)"
in_c -- bash -c "test ! -e /var/run/docker.sock && test ! -e /run/docker.sock && test ! -e '$HOME/.docker'" > /dev/null \
  && ok "there is no Docker socket (INV-7)" || bad "docker socket visible"
in_c -- bash -c 'grep -q "^CapBnd:[[:space:]]*0*$" /proc/self/status && grep -q "^NoNewPrivs:[[:space:]]*1" /proc/self/status && [ "$(id -u)" != 0 ]' > /dev/null \
  && ok "no capabilities, no-new-privileges, non-root (INV-6)" || bad "container privileges"
SELFTEST_SECRET=leak in_c -- bash -c 'env' | grep -q -e SELFTEST_SECRET -e '^SSH_AUTH_SOCK' && bad "host env leaked" || ok "host environment outside the allowlist is not passed (INV-5)"
in_c -- bash -c "! grep -q . ~/.pi/agent/auth.json 2>/dev/null || [ \"\$(cat ~/.pi/agent/auth.json)\" = '{}' ]; test ! -e '$HOME/.pi/agent/auth.json'" > /dev/null \
  && ok "no pi credentials in the container (INV-5)" || bad "credentials visible"
in_c --net local -- bash -c 'curl -s -m 3 http://host.docker.internal:8080/ || curl -s -m 3 http://localhost:8080/' > /dev/null \
  && bad "code under test reached the host gateway" || ok "--net local reaches nothing, not even the host gateway (INV-3, AC-4)"
out="$(in_c --net all -- bash -c 'curl -s -o /dev/null -w %{http_code} -m 5 http://example.com/; curl -s -m 5 https://example.com/ > /dev/null || echo " tls-refused"')"
[[ "$out" == 403*tls-refused* ]] && [[ "$out" == *REFUSED* ]] && ok "the egress proxy refuses and logs a non-allowlisted host (INV-4)" || bad "egress allowlist ($out)"
if curl -s -m 2 -o /dev/null http://localhost:8080/v1/models; then
  in_c --net all -- bash -c 'curl -sf -m 10 -o /dev/null "$SDLC_GATEWAY_URL/v1/models"' > /dev/null \
    && ok "a model role reaches the gateway through the proxy (AC-3)" || bad "gateway via proxy"
fi
in_c -- env SDLC_DOCKER_ENTRY='{"mounts": [{"path": "/tmp", "mode": "rw", "nonce": "absent"}]}' \
  python3 "$SKILL/scripts/docker/entry.py" true > /dev/null; expect 97 "$?" "a mount whose host nonce is not visible stops the run (A-3)"
mkdir -p "$T/sdlc ws,ro=1 \"q\""
"$SBX" --rw "$T/sdlc ws,ro=1 \"q\"" -- bash -c "echo x > '$T/sdlc ws,ro=1 \"q\"/f'" > /dev/null 2>&1
[ -f "$T/sdlc ws,ro=1 \"q\"/f" ] && ok "a path with spaces, commas and quotes mounts as given (FM-12)" || bad "mount quoting"
"$SBX" --rw "$T/sdlc-ws" -- bash -c 'exit 127' > /dev/null 2>&1; expect 127 "$?" "the command's own exit code passes through, even 127 (INV-10)"
find "$T/sdlc-ws" -name '.sdlc-mount-check-*' | grep -q . && bad "mount nonce left behind" || ok "mount nonces are removed"
"$SBX" --rw "$T/sdlc-ws" --net all -- sleep 300 > /dev/null 2>&1 & kp=$!
sleep 6; kill -9 "$kp" 2>/dev/null; wait "$kp" 2>/dev/null; sleep 4
[ -z "$(docker ps -aq --filter label=sdlc)" ] && [ -z "$(docker network ls -q --filter label=sdlc)" ] \
  && ok "a SIGKILLed sandbox leaves no container or network behind (FM-4, AC-5)" || bad "orphaned containers after SIGKILL"
h1="$("${TOOL[@]}" harness-sha)"
h2="$(SDLC_DOCKER_ID_GATE="sha256:$(printf '0%.0s' $(seq 64))" "${TOOL[@]}" harness-sha)"
[ "$h1" != "$h2" ] && ok "a different image changes harness_sha, invalidating harness-bound records (AC-8)" || bad "image not in harness_sha"
fi

mkdir -p "$T/sdlc-iws"; ln -s "$HOME/.pi/agent/settings.json" "$T/sdlc-iws/notes.md"
"${TOOL[@]}" import "$T/sdlc-iws" "$T/imported" notes.md > /dev/null 2>&1 || true
[ ! -e "$T/imported/notes.md" ] && ok "importing a symlinked role output is refused (C1)" || bad "symlink import followed"

RP="$T/rp"; mkdir -p "$RP/.sdlc" "$T/sdlc-ws2"; echo task > "$T/sdlc-ws2/TASK.md"
SDLC_RUNNER_spec='node -e "console.log(\"node-role-ok\")"' SDLC_SANDBOX=required \
  "$SKILL/scripts/run-role.sh" spec "$T/sdlc-ws2" "$T/sdlc-ws2/TASK.md" "$RP/.sdlc" "$RP" > /dev/null 2>&1
grep -q node-role-ok "$RP"/.sdlc/logs/spec-*.log && ok "a Node-based role runs sandboxed and its log lands in .sdlc/" || bad "Node role under sandbox"

cp "$P/gate.config" "$T/gc.keep"; echo 'GATE_MAIN_PATHS="src"' >> "$P/gate.config"
"$S" --project "$P" report > "$T/ov.out" 2>&1; grep -q "overlaps" "$T/ov.out" && ok "main paths that contain the tests are rejected (H6)" || bad "path overlap ($(head -2 "$T/ov.out"))"
cp "$T/gc.keep" "$P/gate.config"
echo 'class X' > "$P/gradle/Impl.kt"
"$S" --project "$P" report > "$T/sk.out" 2>&1; grep -q "skeleton contains source code" "$T/sk.out" && ok "a skeleton holding source code is rejected (C5)" || bad "skeleton source ($(head -2 "$T/sk.out"))"
rm "$P/gradle/Impl.kt"
mkdir -p "$P/.sdlc"; python3 -c 'import fcntl,time,sys; f=open(sys.argv[1],"a"); fcntl.flock(f, fcntl.LOCK_EX); time.sleep(8)' "$P/.sdlc/.mutex.lock" &
sleep 1; "$S" --project "$P" report > "$T/mx.out" 2>&1; grep -q "another sdlc command" "$T/mx.out" && ok "a held project lock blocks a second command (18)" || bad "mutex ($(head -1 "$T/mx.out"))"
wait

if [ "${1:-}" != --quick ]; then
echo "orchestration (fake roles, real sandbox)"
if [ "$CODE_K8S" = k8s ]; then
  # One clear failure when the cluster is not usable (VPN, expired SSO, no isolation probe), not dozens downstream.
  if why="$(SDLC_SANDBOX_BACKEND_CODE=k8s python3 "$SKILL/scripts/k8s/run.py" --check 2>&1)"; then
    export SDLC_SANDBOX_BACKEND_CODE=k8s
    echo "  (code under test on the k8s backend: ${SDLC_K8S_CONTEXT:-?}; roles on local Docker)"
  else
    bad "k8s backend unavailable, orchestration part not run: $(printf '%s' "$why" | head -c 200)"
    echo "selftest: $PASS passed, $FAIL failed"; exit 1
  fi
fi
expect 7 "$(sdlc spec "$T/req.md")" "spec stops for approval (exit 7)"
has "$T/notify.log" "^7 sdlc spec in clamp: waiting for a human decision" "a stop for a human decision sends a notification"
expect 7 "$(sdlc tests)" "tests refuse an unapproved spec"
echo "- NG-99: added by a human before approval (selftest)" >> "$P/.sdlc/spec.md"
expect 0 "$(sdlc approve --relayed 'selftest approval')" "relayed approval recorded"
has "$P/.sdlc/spec.approved" '"session_id"' "approval records the agent session for audit"
has "$P/.sdlc/spec.edited" '"from_sha"' "approve records a human edit of the role's spec (spec.edited)"
has "$P/.sdlc/spec.edit.diff" "NG-99" "the edit is diffed against the role's output (spec.role.md)"
FAKE_TESTS=symlink expect 6 "$(FAKE_TESTS=symlink sdlc tests)" "a test file that is a symlink is refused (C1)"
FAKE_TESTS=impl expect 6 "$(FAKE_TESTS=impl sdlc tests)" "tests role that writes an implementation is rejected (O1)"
has "$P/.sdlc/tests-feedback.md" "implementation" "rejection says why"
expect 0 "$(sdlc tests)" "tests role with TODO() stubs is accepted and locked"
[ ! -e "$P/src/main/kotlin/clamp/Clamp.kt" ] && ok "stubs were discarded, not imported" || bad "stubs imported"
has "$P/.sdlc/inventory-check.json" '"ok": true' "inventory matches a real test run against stubs"
FAKE_BUILD=malicious expect 0 "$(FAKE_BUILD=malicious sdlc build)" "malicious builder: the gate still passes on real tests"
grep -q ESCAPED-SANDBOX "$P"/.sdlc/logs/build-*.log && bad "builder escaped the sandbox" || ok "builder could not write the real project or .sdlc"
grep -q READ-SESSIONS "$P"/.sdlc/logs/build-*.log && bad "builder read agent transcripts" || ok "builder could not read agent transcripts"
grep -q weakened "$P/src/test/kotlin/clamp/ClampTest.kt" && bad "test edit imported" || ok "builder's test edit was not imported"
grep -q "exit 0" "$P/gradlew" && bad "gradlew edit imported" || ok "builder's gradlew edit was not imported (F2)"
has "$P/.sdlc/violations.log" "changed locked files in its workspace" "the attempt is logged as a violation"
grep -q "SCRATCH-FAST-FAIL=.*integrity" "$P"/.sdlc/logs/build-*.log && ok "the malicious builder's own fast scratch gate fails on its tampering" || bad "malicious scratch gate fails"
cp -p "$P/src/main/kotlin/clamp/Clamp.kt" "$T/impl.kt"; rm -rf "$P/src/main/kotlin/clamp"
expect 0 "$(FAKE_BUILD=good sdlc build)" "honest builder: gate passes"
BL="$(ls -t "$P"/.sdlc/logs/build-*.log | head -1)"
grep -q "SCRATCH-FAST-OK" "$BL" \
  && ok "the honest builder's fast scratch gate passes every step but mutation in its workspace" || bad "honest scratch gate passes"
grep -q "TASK-SAYS-FAST" "$BL" && grep -q "SKIPPED-mutation" "$BL" \
  && ok "the builder's task points to --fast, which skips Pitest (fix 5)" || bad "builder fast check ($(grep -c . "$BL"))"
if [ "$CODE_K8S" = k8s ]; then
  grep -q "TASK-SAYS-SDLC-CHECK" "$BL" && grep -qh "check .* exit [0-9]" "$P"/.sdlc/logs/broker-build-*.log 2>/dev/null \
    && ok "the builder's check ran in its warm k8s pod (sdlc-check, broker)" || bad "builder check not through the broker"
fi
CF="$(ls -td "$P"/.sdlc/gate/*-build-r0-i1-confirm-* 2>/dev/null | head -1)"
INC="$(ls -td "$P"/.sdlc/gate/*-build-r0-i1-[0-9]* | head -1)"
incr() { python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("mutation_incremental"))' "$1/gate-report.json"; }
[ -n "$CF" ] && [ "$(incr "$INC")" = True ] && [ "$(incr "$CF")" = False ] \
  && ok "a pass with Pitest history is confirmed by a fresh gate run (fix 6)" || bad "incremental pass confirmed (confirm=$CF)"
grep -q "Incremental analysis reduced number of mutations" "$INC/logs/mutation.log" \
  && ok "Pitest really reused the history of the earlier gate" || bad "Pitest history not used ($(grep -i incremental "$INC/logs/mutation.log" | head -2))"
if [ "$("$SBX" --probe)" = docker ]; then
  ids="$(python3 "$SKILL/scripts/docker/run.py" --ids | sed -n 's/^SDLC_DOCKER_ID_ROLE=//p')"
  python3 -c 'import json,sys; r=json.loads(open(sys.argv[1]).read().splitlines()[-1]); sys.exit(0 if r["sandbox"] == "docker" and (r.get("sandbox_images") or {}).get("SDLC_DOCKER_ID_ROLE") == sys.argv[2] else 1)' \
    "$P/.sdlc/roles.jsonl" "$ids" && ok "roles.jsonl records the docker backend and the role image ID (AC-8)" || bad "roles.jsonl image ID ($(tail -1 "$P/.sdlc/roles.jsonl" | cut -c1-300))"
  if [ "$CODE_K8S" = k8s ]; then
    python3 -c 'import json,sys; s=json.load(open(sys.argv[1]))["sandbox"]; c=s.get("code") or {}; sys.exit(0 if s["backend"] == "docker" and c.get("backend") == "k8s" and "@sha256:" in c.get("image", "") else 1)' \
      "$(ls -td "$P"/.sdlc/gate/*/ | head -1)gate-report.json" && ok "the gate report records roles on docker and code on k8s, image pinned by digest (AC-8)" || bad "gate report backends"
  else
    python3 -c 'import json,sys; s=json.load(open(sys.argv[1]))["sandbox"]; sys.exit(0 if s["backend"] == "docker" and s["images"].get("gate", "").startswith("sha256:") else 1)' \
      "$(ls -td "$P"/.sdlc/gate/*/ | head -1)gate-report.json" && ok "the gate report records the backend and image IDs (AC-8)" || bad "gate report image IDs"
  fi
fi
expect 4 "$(FAKE_BUILD=fileio sdlc build)" "main code using java.io via a wildcard import fails the bytecode check (C3)"
has "$(ls -td "$P"/.sdlc/gate/*/ | head -1)gate-report.md" "forbidden API" "gate report names the forbidden API"
expect 4 "$(FAKE_BUILD=shadow sdlc build)" "a foreign package behind a commented-out package line fails (H4)"
expect 0 "$(FAKE_BUILD=symnotes sdlc build)" "builder symlinks its notes to a file it cannot read"
grep -q "session_id" "$P/.sdlc/build/notes.md" && bad "orchestrator copied the symlink target" || ok "the orchestrator refuses the symlinked notes (C1)"
has "$P/.sdlc/violations.log" "refused import" "the refusal is logged"
FAKE_ATTACK=always expect 0 "$(FAKE_ATTACK=always sdlc attack --round 1)" "attack whose repro is error(\"always\")"
has "$P/.sdlc/attack/round-1/findings.validated.json" "neither an assertion nor raised from main code" "it is discarded: the failure does not come from the code (C8)"
FAKE_ATTACK=wrongtest FAKE_REFEREE=invalid expect 0 "$(FAKE_ATTACK=wrongtest FAKE_REFEREE=invalid sdlc attack --round 1)" "attack with an assertion the spec does not support"
has "$P/.sdlc/attack/round-1/findings.validated.json" "rejected-by-referee" "the referee rejects it (C8)"
ls "$P"/src/test/kotlin/attack/round1/*.kt > /dev/null 2>&1 && bad "rejected repro locked" || ok "a rejected repro is never imported or locked"
FAKE_ATTACK=hang expect 5 "$(FAKE_ATTACK=hang sdlc attack --round 1)" "a repro that times out makes the round incomplete, not clean (C9)"
FAKE_ATTACK=malformed expect 5 "$(FAKE_ATTACK=malformed sdlc attack --round 1)" "malformed findings: round fails, not clean (F6)"
expect 1 "$(sdlc report)" "report is NOT DONE after a failed round"
has "$P/.sdlc/REPORT.md" "did not complete" "report names the failed round"
expect 0 "$(sdlc loop)" "loop: dry round on current code, then calibration"
ls "$P"/.sdlc/calibration/*/calibration.json > /dev/null 2>&1 && ok "calibration ran after the dry round" || bad "calibration ran"
expect 0 "$(sdlc report)" "report is done"
has "$P/.sdlc/REPORT.md" "edited by a human after the spec role" "report lists the approved human spec edit"
mv "$P/.sdlc/spec.edited" "$T/keep.edited"
expect 1 "$(sdlc report)" "a spec edit without its approval record: NOT DONE"
mv "$T/keep.edited" "$P/.sdlc/spec.edited"
expect 0 "$(sdlc report)" "report is done again once the edit record is back"
has "$P/.sdlc/REPORT.md" "HUMAN REVIEW REQUIRED" "escalation from spec ESC-1 forces human review"
has "$P/.sdlc/REPORT.md" "0/1 planted bugs" "calibration result is in the report"
expect 0 "$(FAKE_ATTACK=catch sdlc calibrate)" "calibration with an attacker that finds the planted bug"
C="$(ls -td "$P"/.sdlc/calibration/*/ | head -1)calibration.json"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d["caught"]==1 and d["discriminating"]==1 and d["per_mutant"][0]["caught_by"]==["F1"] else 1)' "$C" \
  && ok "calibration attributes the catch (fails on the mutant, passes on the real code)" || bad "calibration attribution ($(cat "$C"))"
# An upheld dispute goes back to a fresh tests role, then everything is re-checked and relocked.
echo "ClampTest 'AC-1': selftest dispute" > "$P/.sdlc/disputes.md"
expect 1 "$(sdlc report)" "an open dispute blocks DONE"
expect 7 "$(sdlc repair-tests --relayed 'selftest: uphold')" "repair-tests: fresh tests role; repair staged for review (C10)"
[ -s "$P/.sdlc/disputes.md" ] && ok "the dispute stays open until the repair is accepted" || bad "dispute erased early"
has "$P/.sdlc/repair/review.md" "Changed files" "the repair diff is written for review"
expect 0 "$(sdlc accept-repair --relayed 'selftest: accept')" "accept-repair: imported and relocked"
[ ! -s "$P/.sdlc/disputes.md" ] && ls "$P"/.sdlc/disputes.resolved/*-repair.md > /dev/null 2>&1 && ok "the dispute is archived with the ruling" || bad "dispute archived"
expect 0 "$(sdlc loop)" "loop after repair: gate and a fresh attack on the relocked tests"
# A newer gate attempt that never finished counts as a failure (H8).
mkdir -p "$P/.sdlc/gate/99999999-interrupted"; python3 -c 'import json,time; json.dump({"t": time.time()+5}, open("'"$P"'/.sdlc/gate/99999999-interrupted/started.json","w"))'
expect 1 "$(sdlc report)" "an interrupted newer gate attempt makes the report NOT DONE (H8)"
has "$P/.sdlc/REPORT.md" "did not finish" "report says the newest gate did not finish"
rm -rf "$P/.sdlc/gate/99999999-interrupted"
expect 0 "$(sdlc report)" "report is done again once the interrupted attempt is gone"
# Changing the attacker configuration invalidates the dry round (H7).
cp "$P/sdlc.config" "$T/sdlc.keep2"; echo 'SDLC_THINKING_attack="low"' >> "$P/sdlc.config"
expect 1 "$(sdlc report)" "a different attacker configuration invalidates the attack round (H7)"
has "$P/.sdlc/REPORT.md" "different attacker configuration" "report says why"
cp "$T/sdlc.keep2" "$P/sdlc.config"
# A mutation step that hits its time limit stops for a human: the builder is not called again, and
# the rerun gates the current code before it calls the builder (fix 4).
cp "$P/gate.config" "$T/gc.keep3"
printf 'GATE_MUTATION_TIMEOUT=3\nGATE_MUTATION_CMD="sleep 30"\n' >> "$P/gate.config"
expect 0 "$(sdlc relock --relayed 'selftest: a slow mutation step')" "relock after a human gate.config change"
nb="$(ls "$P"/.sdlc/logs/build-*.log | wc -l)"
expect 4 "$(sdlc build)" "a mutation step that times out stops the build loop (exit 4)"
has "$T/last.out" "not a code defect" "the stop says it is a time limit, not a defect"
has "$(ls -td "$P"/.sdlc/gate/*/ | head -1)gate-report.md" "timed out after" "the gate report says the mutation step timed out"
grep -q "evaluator crashed" "$(ls -td "$P"/.sdlc/gate/*/ | head -1)gate-report.md" && bad "timeout reported as an evaluator crash" || ok "a timeout is not reported as an evaluator crash"
[ "$(ls "$P"/.sdlc/logs/build-*.log | wc -l)" -eq $((nb + 1)) ] && ok "the builder ran once and was not called again after the timeout" || bad "builder runs after timeout"
nb="$(ls "$P"/.sdlc/logs/build-*.log | wc -l)"
expect 4 "$(sdlc build)" "the rerun gates the current code first, and stops again on the same limit"
[ "$(ls "$P"/.sdlc/logs/build-*.log | wc -l)" -eq "$nb" ] && ls -d "$P"/.sdlc/gate/*-build-r0-i0-* > /dev/null 2>&1 \
  && ok "on resume the gate ran before the builder, which was not called" || bad "resume gates first"
has "$T/notify.log" "^4 sdlc build in clamp" "a stopped build sends a notification"
cp "$T/gc.keep3" "$P/gate.config"
expect 0 "$(sdlc relock --relayed 'selftest: restore gate.config')" "relock after restoring gate.config"
expect 0 "$(sdlc build)" "after the limit is fixed, the resumed build passes on the current code"
[ "$(ls "$P"/.sdlc/logs/build-*.log | wc -l)" -eq "$nb" ] && ok "the passing resume needed no builder run" || bad "resume pass without builder"
# Staleness: code, spec and lock changes all invalidate completion.
cp "$P/src/main/kotlin/clamp/Clamp.kt" "$T/keep.kt"; echo "// change" >> "$P/src/main/kotlin/clamp/Clamp.kt"
expect 1 "$(sdlc report)" "changed code after the gate: NOT DONE (F8/F13)"
mv "$T/keep.kt" "$P/src/main/kotlin/clamp/Clamp.kt"
cp "$P/.sdlc/spec.md" "$T/keep.md"; echo "- NG-2: extra" >> "$P/.sdlc/spec.md"
expect 1 "$(sdlc report)" "changed spec after approval: NOT DONE (F5)"
mv "$T/keep.md" "$P/.sdlc/spec.md"
rm "$P/.sdlc/gate/"*/gate-report.md
expect 1 "$(sdlc report)" "missing gate-report.md: NOT DONE (F7)"
unset SDLC_SANDBOX_BACKEND_CODE
fi

echo "gate (fail closed)"
G2="$T/g"; mkdir -p "$G2"
gate_with() {  # $1 = gate.config lines; runs a gate over a trivial project; echoes exit code
  rm -rf "$G2"/*; mkdir -p "$G2/.sdlc"; printf '%s\n' "$1" > "$G2/gate.config"
  "${TOOL[@]}" lock "$G2" "$G2/.sdlc/tests.lock" > /dev/null
  "$G" --project "$G2" --label t > "$T/gate.out" 2>&1; echo $?
}
BASE='GATE_TEST_INVENTORY=""
GATE_TYPECHECK_CMD=true
GATE_MUTATION_CMD=true
GATE_FUZZ_MODE=off'
expect 1 "$(gate_with "$BASE
GATE_STEPS=\"integrity typecheck static\"
GATE_STATIC_CMD=\"echo '<broken' > r.xml\"
GATE_STATIC_REPORT=r.xml")" "a crashed evaluator plus missing mandatory steps fails (F3)"
expect 1 "$(gate_with "$BASE
GATE_STEPS=\"integrity typecheck test mutation static\"
GATE_TEST_MODE=requird
GATE_TEST_CMD=true
GATE_STATIC_CMD=true")" "a misspelled mode fails (F10)"
expect 1 "$(gate_with "$BASE
GATE_STEPS=\"integrity typecheck test mutation static\"
GATE_MUTATION_MODE=optional
GATE_TEST_CMD=true
GATE_STATIC_CMD=true")" "a mandatory step made optional fails (F10)"
# Time limits and unreadable reports are failures with a reason, never evaluator crashes (fix 4).
expect 1 "$(gate_with "$BASE
GATE_STEPS=\"integrity mutation\"
GATE_MUTATION_TIMEOUT=2
GATE_MUTATION_CMD=\"sleep 20\"")" "a mutation step over its time limit fails"
gr="$(ls -td "$G2"/.sdlc/gate/*/ | head -1)"
python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); sys.exit(0 if d["timed_out"] == ["mutation"] else 1)' "$gr/gate-report.json" \
  && grep -q "timed out after" "$gr/gate-report.md" && ! grep -q "evaluator crashed" "$gr/gate-report.md" \
  && ok "... and is reported as timed out (report.timed_out), not as an evaluator crash" || bad "timeout report ($(grep '^| mutation' "$gr/gate-report.md"))"
expect 1 "$(gate_with "$BASE
GATE_STEPS=\"integrity mutation\"
GATE_MUTATION_CMD=\"mkdir -p build/reports/pitest && printf '<mutations><mutation' > build/reports/pitest/mutations.xml\"")" "a truncated Pitest report fails"
has "$(ls -td "$G2"/.sdlc/gate/*/ | head -1)gate-report.md" "mutation report unreadable" "... with 'report unreadable', not an evaluator crash"
expect 1 "$(gate_with "$BASE
GATE_STEPS=\"integrity mutation\"
GATE_MUTATION_CMD=\"mkdir -p build/reports/pitest && printf '<!DOCTYPE m [<!ENTITY x \\\"y\\\">]><mutations/>' > build/reports/pitest/mutations.xml\"")" "a report with a DTD fails"
has "$(ls -td "$G2"/.sdlc/gate/*/ | head -1)gate-report.md" "entity declarations are not allowed" "... because DTDs and entities are refused in reports written by code under test"
# Incremental Pitest: the gate supplies the history, marks the run, and --fresh ignores it (fix 6).
HC='GATE_STEPS="integrity mutation"
GATE_MUTATION_CMD="mkdir -p build/reports/pitest; [ -f build/sdlc-pitest-history/in.bin ] && echo HAD-HIST\"\"ORY; echo h > build/sdlc-pitest-history/out.bin; printf %s \"<mutations><mutation detected=\\\"true\\\" status=\\\"KILLED\\\"><mutatedClass>a.A</mutatedClass><mutatedMethod>f</mutatedMethod><lineNumber>1</lineNumber><mutator>M</mutator></mutation></mutations>\" > build/reports/pitest/mutations.xml"'
rm -rf "$T/hist"
gate_hist() {  # $@ = extra gate.sh args; history dir $T/hist; echoes "mutation-status incremental had-history"
  # (the report itself fails: this gate omits mandatory steps on purpose)
  rm -rf "$G2"/*; mkdir -p "$G2/.sdlc"; printf '%s\n' "$BASE" "$HC" > "$G2/gate.config"
  "${TOOL[@]}" lock "$G2" "$G2/.sdlc/tests.lock" > /dev/null
  "$G" --project "$G2" --label h "$@" > "$T/gate.out" 2>&1
  local d; d="$(ls -td "$G2"/.sdlc/gate/*/ | head -1)"
  echo "$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["status"])' "$d/steps/02-mutation.json") $(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["mutation_incremental"])' "$d/gate-report.json") $(grep -c HAD-HISTORY "$d/logs/mutation.log")"
}
expect "pass False 0" "$(gate_hist --mutation-history "$T/hist")" "first gate with history: fresh run, history kept"
[ -s "$T/hist/history.bin" ] && ok "the gate keeps Pitest's output history" || bad "history kept"
expect "pass True 1" "$(gate_hist --mutation-history "$T/hist")" "second gate: Pitest gets the history, and the report marks it incremental"
expect "pass False 0" "$(gate_hist --mutation-history "$T/hist" --fresh)" "--fresh: history not read, report not incremental"
expect "pass False 0" "$(gate_hist)" "no --mutation-history: no history"
expect 1 "$(gate_with "$BASE
GATE_STEPS=\"integrity static mutation\"
GATE_STATIC_CMD=\"mkdir -p build/sdlc-pitest-history && echo forged > build/sdlc-pitest-history/in.bin\"
$HC")" "(mandatory steps missing: the gate fails, as intended here)"
grep -q HAD-HISTORY "$(ls -td "$G2"/.sdlc/gate/*/ | head -1)logs/mutation.log" && bad "a history file planted by an earlier step reached Pitest" \
  || ok "a history file planted by an earlier step (code under test) never reaches Pitest"
# --fast: builder feedback without Pitest; scratch only.
rm -rf "$G2"/*; mkdir -p "$G2/.sdlc"; printf '%s\n' "$BASE" 'GATE_STEPS="integrity mutation"' > "$G2/gate.config"
"${TOOL[@]}" lock "$G2" "$G2/.sdlc/tests.lock" > /dev/null
"$G" --project "$G2" --scratch --fast > "$T/gate.out" 2>&1
has "$G2/.sdlc/build/gate-scratch/gate-report.md" "skipped by --fast" "--scratch --fast skips the mutation step and says so"
"$G" --project "$G2" --fast > "$T/gate.out" 2>&1; expect 2 "$?" "--fast without --scratch is refused (a fast run is never evidence)"

echo "k8s backend copy-in/copy-out (unit tests, no cluster)"
python3 "$ROOT/tests/validation_unit_test.py" > "$T/validation-unit.out" 2>&1 && ok "sandbox failures are incomplete, never no-build: $(grep -o 'Ran [0-9]* tests' "$T/validation-unit.out") pass" \
  || bad "validation unit tests: $(tail -3 "$T/validation-unit.out" | tr '\n' ' ')"
python3 "$ROOT/tests/broker_unit_test.py" > "$T/broker-unit.out" 2>&1 && ok "check broker: requests, sync, delete, shim protocol: $(grep -o 'Ran [0-9]* tests' "$T/broker-unit.out") pass" \
  || bad "check broker unit tests: $(tail -3 "$T/broker-unit.out" | tr '\n' ' ')"
python3 "$ROOT/tests/shard_unit_test.py" > "$T/shard-unit.out" 2>&1 && ok "sharded mutation: plan, quoting, merge partition checks: $(grep -o 'Ran [0-9]* tests' "$T/shard-unit.out") pass" \
  || bad "sharded mutation unit tests: $(tail -3 "$T/shard-unit.out" | tr '\n' ' ')"
python3 "$ROOT/tests/k8s_unit_test.py" > "$T/k8s-unit.out" 2>&1 && ok "k8s safe_extract/pack/context: $(grep -o 'Ran [0-9]* tests' "$T/k8s-unit.out") pass (malicious archives refused, host untouched)" \
  || bad "k8s unit tests ($(tail -3 "$T/k8s-unit.out" | tr '\n' ' '))"

python3 "$ROOT/tests/rule_validate_unit_test.py" > "$T/rule-validate.out" 2>&1 && ok "rule validation (B2): $(grep -o 'Ran [0-9]* tests' "$T/rule-validate.out") pass (vacuous, flagging and unlisted-match rules refused)" \
  || bad "rule validation unit tests ($(grep -E '^(FAIL|ERROR)' "$T/rule-validate.out" | head -3 | tr '\n' ' '))"
python3 "$ROOT/tests/gate_rules_unit_test.py" > "$T/gate-rules.out" 2>&1 && ok "GATE_RULES static step: $(grep -o 'Ran [0-9]* tests' "$T/gate-rules.out") pass (a match, no rules, no ast-grep, unlocked dir all fail)" \
  || bad "GATE_RULES unit tests ($(grep -E '^(FAIL|ERROR)' "$T/gate-rules.out" | head -3 | tr '\n' ' '))"

python3 "$ROOT/tests/json_io_unit_test.py" > "$T/json-io.out" 2>&1 && ok "file/JSON I/O fails closed: $(grep -o 'Ran [0-9]* tests' "$T/json-io.out") pass (malformed input, unrecordable lock, stale results)" \
  || bad "JSON I/O unit tests ($(grep -E '^(FAIL|ERROR)' "$T/json-io.out" | head -3 | tr '\n' ' '))"

python3 "$ROOT/tests/planted_recurrence_test.py" > "$T/planted.out" 2>&1 && ok "planted recurrence of each consolidated rule fails GATE_RULES, its fix passes: $(grep -o 'Ran [0-9]* tests' "$T/planted.out") pass" \
  || bad "planted recurrence tests ($(grep -E '^(FAIL|ERROR)' "$T/planted.out" | head -3 | tr '\n' ' '))"

python3 "$ROOT/tests/consolidate_unit_test.py" > "$T/consolidate.out" 2>&1 && ok "consolidate step (B1/B2, orchestrator side): $(grep -o 'Ran [0-9]* tests' "$T/consolidate.out") pass (only B2-accepted rules staged; escapes and duplicates refused)" \
  || bad "consolidate unit tests ($(grep -E '^(FAIL|ERROR)' "$T/consolidate.out" | head -3 | tr '\n' ' '))"

python3 "$ROOT/tests/gate_step_rules_test.py" > "$T/gate-step-rules.out" 2>&1 && ok "gate-step static with GATE_RULES end to end: $(grep -o 'Ran [0-9]* tests' "$T/gate-step-rules.out") pass (a match fails the recorded step, bad setups fail it)" \
  || bad "gate-step GATE_RULES tests ($(grep -E '^(FAIL|ERROR)' "$T/gate-step-rules.out" | head -3 | tr '\n' ' '))"
python3 "$ROOT/tests/consolidate_run_test.py" > "$T/consolidate-run.out" 2>&1 && ok "sdlc.sh consolidate end to end (fake role): $(grep -o 'Ran [0-9]* tests' "$T/consolidate-run.out") pass (accepted rules staged, never adopted; failures reported)" \
  || bad "consolidate run tests ($(grep -E '^(FAIL|ERROR)' "$T/consolidate-run.out" | head -3 | tr '\n' ' '))"
python3 "$ROOT/tests/adopt_rules_unit_test.py" > "$T/adopt-unit.out" 2>&1 && python3 "$ROOT/tests/adopt_run_test.py" > "$T/adopt-run.out" 2>&1 \
  && ok "adopt-rules: $(grep -o 'Ran [0-9]* tests' "$T/adopt-unit.out") + $(grep -o 'Ran [0-9]* tests' "$T/adopt-run.out") pass (atomic batch, human only, locked, audited)" \
  || bad "adopt-rules tests ($(grep -hE '^(FAIL|ERROR)' "$T/adopt-unit.out" "$T/adopt-run.out" | head -3 | tr '\n' ' '))"
python3 "$ROOT/tests/gated_list_unit_test.py" > "$T/gated-unit.out" 2>&1 && python3 "$ROOT/tests/gated_attack_test.py" > "$T/gated-attack.out" 2>&1 \
  && ok "attacker told which classes are gated (B4): $(grep -o 'Ran [0-9]* tests' "$T/gated-unit.out") + $(grep -o 'Ran [0-9]* tests' "$T/gated-attack.out") pass (id and message only; round identity follows the list)" \
  || bad "gated-class tests ($(grep -hE '^(FAIL|ERROR)' "$T/gated-unit.out" "$T/gated-attack.out" | head -3 | tr '\n' ' '))"
python3 "$ROOT/tests/consolidation_report_test.py" > "$T/consolidation-report.out" 2>&1 && ok "report: consolidation section (B5): $(grep -o 'Ran [0-9]* tests' "$T/consolidation-report.out") pass (from files only; absent when nothing consolidated)" \
  || bad "consolidation report tests ($(grep -E '^(FAIL|ERROR)' "$T/consolidation-report.out" | head -3 | tr '\n' ' '))"
# Opt-in (minutes): every mutant of the new rule/consolidate/export/IO code is killed or a reviewed equivalent.
if [ "${SDLC_SELFTEST_MUTATION:-}" = 1 ]; then
  python3 "$ROOT/tests/mutation_check.py" > "$T/mutation.out" 2>&1 && ok "mutation gate: $(grep -c 'mutants killed' "$T/mutation.out") modules, no surviving mutant" \
    || bad "mutation gate ($(grep SURVIVED "$T/mutation.out" | head -3 | tr '\n' ' '))"
fi

# Review findings consolidated into deterministic rules: each rule must
# still fire on its finding's pre-fix code (tests/rule-tests) and the harness scripts must match none of them.
# A missing ast-grep fails: a check that silently does not run would read as a pass.
echo "review findings as deterministic rules (tests/rules, ast-grep)"
SG="$(command -v ast-grep || ls "$HOME"/.pi/agent/npm/node_modules/@ast-grep/cli-*/ast-grep 2>/dev/null | head -1)"
if [ -z "$SG" ]; then
  bad "ast-grep not found (npm i -g @ast-grep/cli): the review-finding rules cannot run"
else
  (cd "$ROOT/tests" && "$SG" test --skip-snapshot-tests) > "$T/rules-test.out" 2>&1 \
    && ok "each rule fires on its finding's pre-fix code and not on the fix: $(grep -o '[0-9]* passed' "$T/rules-test.out")" \
    || bad "rule self-tests: $(grep -E 'FAIL|failed' "$T/rules-test.out" | head -3 | tr '\n' ' ')"
  (cd "$ROOT/tests" && "$SG" scan --error ../scripts) > "$T/rules-scan.out" 2>&1 \
    && ok "the harness scripts match none of the $(ls "$ROOT"/tests/rules/*.yml | wc -l | tr -d ' ') rules" \
    || bad "rule matches in the harness scripts: $(grep -E '^error' "$T/rules-scan.out" | head -3 | tr '\n' ' ')"
fi

if [ -n "${SDLC_TEST_K8S_CONTEXT:-}" ]; then
  echo "k8s backend on kind ($SDLC_TEST_K8S_CONTEXT)"
  if "$ROOT/tests/k8s_kind_test.sh" > "$T/k8s-kind.out" 2>&1; then ok "k8s mechanics on kind: $(grep -c '^  ok' "$T/k8s-kind.out") checks pass"
  else bad "k8s mechanics on kind: $(grep '^  FAIL' "$T/k8s-kind.out" | head -3 | tr '\n' ';')"; fi
fi
echo "lock and file sets"
L="$T/l"; mkdir -p "$L/src/test/kotlin" "$L/src/main"; touch "$L/build.gradle.kts" "$L/src/test/kotlin/A.kt"
"${TOOL[@]}" lock "$L" "$T/l.lock.json" > /dev/null
mkdir -p "$L/src/test/kotlin/out"; echo x > "$L/src/test/kotlin/out/Sneaky.kt"
"${TOOL[@]}" verify-lock "$L" "$T/l.lock.json" > /dev/null && bad "file under src/test/.../out/ escaped the lock" || ok "a file under src/test/kotlin/out/ is caught (F11)"
rm -rf "$L/src/test/kotlin/out"; mkdir -p "$L/build"; echo x > "$L/build/gen.txt"
"${TOOL[@]}" verify-lock "$L" "$T/l.lock.json" > /dev/null && ok "real build output next to a build script is ignored" || bad "build output ignored"
echo x >> "$L/build.gradle.kts"
"${TOOL[@]}" verify-lock "$L" "$T/l.lock.json" > /dev/null && bad "build script edit escaped the lock" || ok "build-script edits are caught"

echo "findings"
F="$T/f.json"
for bad_json in '{"findings": ""}' '{"findings": {}}' '{"findings": [{"id": 1}]}' '[]' 'not json'; do
  printf '%s' "$bad_json" > "$F"
  "${TOOL[@]}" check-findings "$F" > /dev/null 2>&1 && bad "accepted malformed: $bad_json" || ok "rejects malformed findings: $bad_json"
done
echo '{"findings": [], "attempted": ["x"]}' > "$F"
"${TOOL[@]}" check-findings "$F" > /dev/null && ok "accepts an empty, well-formed findings file" || bad "accepts empty findings"
"${TOOL[@]}" validate-findings --project "$T" --workspace "$T" --findings f.json --out "$T/v" --spec "$T/req.md" --runs 1 > /dev/null 2>&1 \
  && bad "runs=1 accepted" || ok "repro runs < 2 is refused (F9)"
cp "$P/sdlc.config" "$T/sdlc.keep"; echo "SDLC_MAX_ROUNDS=0" >> "$P/sdlc.config"
"$S" --project "$P" report > "$T/mr.out" 2>&1; mr=$?; cp "$T/sdlc.keep" "$P/sdlc.config"
[ $mr = 1 ] && grep -q "SDLC_MAX_ROUNDS=0 must be an integer in 1..3" "$T/mr.out" && ok "SDLC_MAX_ROUNDS=0 is refused with a config error (F10)" || bad "MAX_ROUNDS=0 refused ($(cat "$T/mr.out"))"

echo "path safety and inputs (cycle 2)"
M="$T/mut"; mkdir -p "$M/src/main/kotlin" "$M/x"; echo 'val a = 1' > "$M/src/main/kotlin/A.kt"; echo keep > "$M/gate.config"
printf '[{"name":"evil","file":"src/main/../gate.config","old":"keep","new":"pwned"}]' > "$T/evil.json"
"${TOOL[@]}" mutate --root "$M" --mutants "$T/evil.json" > /dev/null 2>&1; grep -q keep "$M/gate.config" && ok "a planted bug cannot escape the main paths (C2)" || bad "mutant path traversal"
printf '{"findings":[{"id":"../../x","title":"t","spec_refs":["INV-1"],"expected":"a","actual":"b"}]}' > "$F"
"${TOOL[@]}" check-findings "$F" > /dev/null 2>&1 && bad "path-like finding id accepted" || ok "a finding id with path characters is rejected (H11)"
printf '{"findings":[{"id":"F1","title":"t","spec_refs":["whatever"],"expected":"a","actual":"b","severity":"urgent"}]}' > "$F"
"${TOOL[@]}" check-findings "$F" > /dev/null 2>&1 && bad "bad severity/spec ref accepted" || ok "invalid severity and spec-ref syntax are rejected (14)"
J="$T/judge/design"; mkdir -p "$J"; printf '# Spec\n- INV-1: x\n| FM-1 | y |\n' > "$T/judge/spec.md"
echo a > "$J/candidate-1.md"; echo b > "$J/candidate-2.md"; echo d > "$J/decision.md"
echo '{"criteria":["INV-1","FM-1"],"candidates":{"candidate-1":{"INV-1":{"score":3,"evidence":"e"},"FM-1":{"score":3,"evidence":"e"}},"candidate-2":{"INV-1":{"score":1,"evidence":"e"},"FM-1":{"score":1,"evidence":"e"}}},"winner":"candidate-2"}' > "$J/scores.json"
"${TOOL[@]}" judge-check --dir "$J" --spec "$T/judge/spec.md" > /dev/null 2>&1 && bad "judge picked a lower-scoring winner" || ok "a winner without the highest total is rejected (15)"
sedi 's/"winner":"candidate-2"/"winner":"candidate-1"/' "$J/scores.json"
"${TOOL[@]}" judge-check --dir "$J" --spec "$T/judge/spec.md" > /dev/null 2>&1 && ok "a complete, consistent score matrix is accepted" || bad "valid judge output accepted"
printf 'fun interface Clock { fun now(): Long }\nclass D { fun go(): Int = TODO() }\nval cache = mutableMapOf<String, Int>()\n' > "$M/src/main/kotlin/S.kt"
"${TOOL[@]}" stub-check "$M" src/main/kotlin/S.kt > "$T/sc.out" 2>&1; grep -q "property initializer" "$T/sc.out" && ! grep -q "Clock" "$T/sc.out" \
  && ok "stub check: fun interface allowed, executable initializer rejected (12)" || bad "stub check ($(cat "$T/sc.out"))"

echo "selftest: $PASS passed, $FAIL failed"
[ "$FAIL" = 0 ]
