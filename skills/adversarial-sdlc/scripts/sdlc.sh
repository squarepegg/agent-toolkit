#!/usr/bin/env bash
# sdlc.sh: orchestrator for the adversarial-sdlc skill.
#
# Usage: sdlc.sh [--project DIR] COMMAND [ARGS]
#
#   init [--scaffold kotlin --package PKG]   create gate.config, sdlc.config (+ Kotlin skeleton)
#   spec REQUIREMENTS.md                     phase 1 (archives any earlier run), then STOP
#   approve [--relayed "user's words"]       human approves the spec
#   tests                                    phase 2: tests from SPEC.md only; checked; locked
#   accept-gaps [--relayed "..."]            human accepts the spec gaps the tests role reported
#   repair-tests --relayed "ruling"          human upholds a dispute: a fresh tests role fixes the disputed tests
#   accept-repair --relayed "..."            human accepts the repaired tests (review .sdlc/repair/review.md)
#   design                                   phase 3 (optional): designer + judge
#   build [--round N]                        phase 4: builder in a copy until the gate passes
#   attack --round N                         phase 5: fresh attacker; repro-validated findings
#   calibrate [MUTANTS.json]                 measure the attacker on planted bugs (default: planter role)
#   consolidate --round N                    propose and validate deterministic checks for round N's findings (staged only)
#   adopt-rules [NAME.yml..] --relayed "..."  human only: adopt staged rules into the locked build (one batch; --dry-run to plan)
#   loop                                     phases 4-6 (resumable)
#   escalate | report | status
#   run                                      tests -> [design] -> loop -> report
#   gate [--label L]                         run the gate on a snapshot
#   relock --relayed "reason"                human only: re-lock after a human edit
#   revalidate --round N                     validate round N's saved findings again (after an infrastructure failure)
#   watch [--once] [--every S]               live view of a run: stages, what runs now, estimated completion
#   resolve-disputes --relayed "ruling"      human only: archive disputes after ruling
#
# Exit codes: 0 done; 1 error or not done; 3 dispute; 4 gate still failing; 5 attack incomplete;
#             6 tests phase rejected; 7 waiting for a human (approval, spec gaps).
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL=(python3 "$SKILL_DIR/scripts/sdlc_tool.py")
SBX="$SKILL_DIR/scripts/sandbox.sh"
PROJECT="$PWD"
if [ "${1:-}" = "--project" ]; then PROJECT="$2"; shift 2; fi
PROJECT="$(cd "$PROJECT" && pwd -P)"
RUN="$PROJECT/.sdlc"
CMD="${1:-help}"; shift || true
export SDLC_PROJECT="$PROJECT"

die() { echo "sdlc: $*" >&2; exit 1; }
say() { echo "sdlc: $*"; }
j() { python3 -c "import json,sys; d=json.load(open(sys.argv[1])); print(eval(sys.argv[2], {}, {'d': d}))" "$@"; }

# ------------------------------------------------------------------ config, identity, locking

load_config() {
  [ -f "$PROJECT/gate.config" ] || die "no gate.config; run: sdlc.sh init"
  set -a
  GATE_MAIN_PATHS="src/main"; GATE_TEST_PATHS="src/test"; GATE_TEST_INVENTORY=""
  export GATE_DOC_PATHS=""  # read by sdlc_tool.py (lock and fingerprint)
  GATE_SKELETON_PATHS="build.gradle.kts settings.gradle.kts gradle.properties gradlew gradlew.bat gradle"
  GATE_REPRO_DIR="src/test/kotlin/attack"; GATE_REPRO_CMD="./gradlew test {tests}"; GATE_JUNIT_DIR="build/test-results/test"
  GATE_TEST_CMD=""
  # shellcheck disable=SC1091
  . "$SKILL_DIR/templates/sdlc.config"
  # shellcheck disable=SC1091
  [ -f "$PROJECT/sdlc.config" ] && . "$PROJECT/sdlc.config"
  # shellcheck disable=SC1091
  . "$PROJECT/gate.config"
  set +a
  local n
  for n in SDLC_MAX_ROUNDS:1:3 SDLC_BUILD_MAX_ITERS:1:20 SDLC_REPRO_RUNS:2:10 SDLC_DESIGN_CANDIDATES:2:3 \
           SDLC_TESTS_ATTEMPTS:1:5 SDLC_ATTACK_RETRIES:0:5; do
    IFS=: read -r var lo hi <<< "$n"
    [[ "${!var}" =~ ^[0-9]+$ ]] && [ "${!var}" -ge "$lo" ] && [ "${!var}" -le "$hi" ] \
      || die "$var=${!var} must be an integer in $lo..$hi"
  done
  case "$SDLC_SANDBOX" in auto|required|off) ;; *) die "SDLC_SANDBOX must be auto|required|off" ;; esac
  case "$SDLC_CALIBRATE" in auto|off) ;; *) die "SDLC_CALIBRATE must be auto|off" ;; esac
  [ -n "${GATE_TEST_INVENTORY:-}" ] || die "GATE_TEST_INVENTORY must be set in gate.config"
  python3 - "$PROJECT" <<'PY' || die "path configuration rejected (see above)"
import os, sys
root = sys.argv[1]
sets = {k: os.environ.get(k, "").split() for k in
        ("GATE_MAIN_PATHS", "GATE_TEST_PATHS", "GATE_SKELETON_PATHS", "GATE_DOC_PATHS", "GATE_GENERATED_PATHS")}
real = {k: [(p, os.path.realpath(os.path.join(root, p))) for p in v] for k, v in sets.items()}
bad = []
names = list(real)
for i, a in enumerate(names):
    for b in names[i + 1:]:
        for pa, ra in real[a]:
            for pb, rb in real[b]:
                if ra == rb or ra.startswith(rb + os.sep) or rb.startswith(ra + os.sep):
                    bad.append(f"{a} '{pa}' overlaps {b} '{pb}'")
if not sets["GATE_MAIN_PATHS"] or not sets["GATE_TEST_PATHS"]:
    bad.append("GATE_MAIN_PATHS and GATE_TEST_PATHS must both be set")
src_ext = (".kt", ".java", ".scala", ".groovy", ".py", ".ts", ".js", ".go", ".rs", ".cs")
if os.environ.get("SDLC_SKELETON_ALLOW_SOURCES") != "1":
    for p, r in real["GATE_SKELETON_PATHS"]:
        walk = os.walk(r) if os.path.isdir(r) else [(os.path.dirname(r), [], [os.path.basename(r)])] if os.path.exists(r) else []
        for dp, _, fs in walk:
            for f in fs:
                if f.endswith(src_ext):
                    bad.append(f"skeleton contains source code ({os.path.relpath(os.path.join(dp, f), root)}): "
                               "the tests role would see it (SDLC_SKELETON_ALLOW_SOURCES=1 to override)")
for x in bad:
    print("sdlc: " + x, file=sys.stderr)
sys.exit(1 if bad else 0)
PY
  [ -n "${GATE_MAIN_PACKAGES:-}" ] || die "GATE_MAIN_PACKAGES must be set in gate.config (packages the builder owns)"
  return 0
}

take_mutex() {  # flock on fd 9: held while this shell lives, released by the kernel, no stale locks
  mkdir -p "$RUN"; [ -L "$RUN/.mutex" ] && rm -f "$RUN/.mutex"
  exec 9>>"$RUN/.mutex.lock"
  python3 -c 'import fcntl; fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)' 2>/dev/null \
    || die "another sdlc command is running on this project"
}

sha256_cmd() { if command -v shasum > /dev/null; then shasum -a 256; else sha256sum; fi; }
sha() { sha256_cmd < "$1" | cut -d' ' -f1; }
spec_sha() { [ -f "$RUN/spec.md" ] && sha "$RUN/spec.md" || echo none; }
approved_sha() { [ -f "$RUN/spec.approved" ] && j "$RUN/spec.approved" 'd["sha256"]' || echo none; }
lock_sha() { [ -f "$RUN/tests.lock" ] && sha "$RUN/tests.lock" || echo none; }
fp() { "${TOOL[@]}" fingerprint "$PROJECT"; }
# gated_list: the classes the adopted rules already check (rule id and message), or nothing.
gated_list() {
  [ -n "${GATE_RULES:-}" ] && [ -d "$PROJECT/$GATE_RULES" ] || return 0
  python3 "$SKILL_DIR/scripts/adopt_rules.py" --gated-classes "$PROJECT/$GATE_RULES"
}
attacker_cfg() {  # everything that decides how the attacker (and referee) behaves
  local model; model="$(attackers)"; local ext
  [ -n "$model" ] || model="default:$(python3 -c 'import json,os; s=json.load(open(os.path.expanduser("~/.pi/agent/settings.json"))); print(s.get("defaultProvider",""), s.get("defaultModel",""))' 2>/dev/null)"
  # shellcheck disable=SC2086
  ext="$(python3 - ${SDLC_PI_EXTENSIONS:-} <<'PY'
import hashlib, os, sys
h = hashlib.sha256()
for root in sys.argv[1:]:
    for dp, dn, fs in os.walk(root):
        dn[:] = sorted(d for d in dn if d not in (".git", "node_modules"))
        for f in sorted(fs):
            p = os.path.join(dp, f)
            h.update(p.encode() + open(p, "rb").read())
print(h.hexdigest())
PY
)"
  printf '%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s|%s' "$model" "${SDLC_THINKING_attack:-}" "${SDLC_RUNNER_attack:-}" \
    "${SDLC_TOOLS_attack:-}" "$(sha "$SKILL_DIR/roles/attack.md")" "$("${SDLC_PI_BIN:-pi}" --version 2>/dev/null)" \
    "$ext" "${SDLC_PI_EXTRA_ARGS:-}" "${SDLC_SANDBOX_NET_ROLES:-all}" "${SDLC_REFEREE:-on}" "${SDLC_MODEL_referee:-}" \
    "$(sha "$SKILL_DIR/roles/referee.md")" | { cat; local g; g="$(gated_list)"; [ -z "$g" ] || printf '|gated:%s' "$(printf '%s' "$g" | sha256_cmd | cut -d' ' -f1)"; } | sha256_cmd | cut -d' ' -f1
}

require_approval() {
  [ -f "$RUN/spec.md" ] || die "no spec; run: sdlc.sh spec REQUIREMENTS.md"
  [ "$(approved_sha)" = "$(spec_sha)" ] || { say "spec not approved (or changed since). A human must approve it."; exit 7; }
}
require_tests() {
  [ -f "$RUN/tests.lock" ] || die "tests not written; run: sdlc.sh tests"
  [ "$(j "$RUN/tests.lock" 'd.get("spec_sha","")')" = "$(spec_sha)" ] || die "tests were written for a different spec; rerun: sdlc.sh tests"
}
gaps_open() {
  [ -s "$RUN/spec-gaps.md" ] || return 1
  [ -f "$RUN/spec-gaps.accepted" ] && [ "$(j "$RUN/spec-gaps.accepted" 'd["sha256"]')" = "$(sha "$RUN/spec-gaps.md")" ] && return 1
  return 0
}
require_no_gaps() { if gaps_open; then say "spec gaps await a human (.sdlc/spec-gaps.md): amend the spec, or run accept-gaps"; exit 7; fi; }

human_ok() {  # $1 = what; remaining args: [--relayed TEXT]
  local what="$1"; shift
  if [ "${1:-}" = "--relayed" ]; then [ -n "${2:-}" ] || die "$what --relayed needs the user's words"; HOW="relayed by agent"; NOTE="$2"
  elif [ -t 0 ]; then printf 'Type YES to %s: ' "$what"; read -r ans; [ "$ans" = YES ] || die "not confirmed"; HOW="typed at terminal"; NOTE=""
  else die "$what must be typed at a terminal, or use --relayed \"<the user's words>\""; fi
}
write_human_record() {  # $1 = out file, $2 = sha of the thing approved
  python3 - "$1" "$2" "$HOW" "$NOTE" <<'EOF'
import json, os, sys, time
p, sha, how, note = sys.argv[1:]
json.dump({"sha256": sha, "how": how, "note": note, "user": os.environ.get("USER", "unknown"),
           "session_id": os.environ.get("PI_SESSION_ID"), "session_file": os.environ.get("PI_SESSION_FILE"),
           "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, open(p, "w"), indent=2)
EOF
}

new_ws() { mktemp -d "${TMPDIR:-/tmp}/sdlc-$1-XXXXXX"; }
# copy_in WS PATH... : copy those project paths (file-set rules apply) into the workspace.
copy_in() { local ws="$1"; shift; [ $# -gt 0 ] && "${TOOL[@]}" copy "$PROJECT" "$ws" "$@" > /dev/null; return 0; }
# broker_for ROLE: does this role run its own checks through a warm k8s runner pod (scripts/k8s/broker.py)?
broker_for() { [ "${SDLC_SANDBOX_BACKEND_CODE:-}" = k8s ] && [[ " ${SDLC_BROKER_ROLES:-build attack} " == *" $1 "* ]]; }
# check_prefix ROLE: what a role's task puts in front of a check command (sdlc-check sends it to the warm pod).
check_prefix() { broker_for "$1" && printf '%s ' "$SKILL_DIR/scripts/sdlc-check"; return 0; }
role() {  # ROLE WORKSPACE TASK
  local bpid="" rc=0
  if broker_for "$1"; then
    mkdir -p "$2/.sdlc-remote" "$RUN/logs"
    python3 "$SKILL_DIR/scripts/k8s/broker.py" serve --ws "$2" --role "$1" >> "$RUN/logs/broker-$1-$(basename "$2").log" 2>&1 9>&- &
    bpid=$!
  fi
  SDLC_REMOTE_WS="$2" "$SKILL_DIR/scripts/run-role.sh" "$1" "$2" "$3" "$RUN" "$PROJECT" 9>&- || rc=$?
  if [ -n "$bpid" ]; then
    touch "$2/.sdlc-remote/stop"; kill -TERM "$bpid" 2>/dev/null; wait "$bpid" 2>/dev/null
  fi
  return "$rc"
}
# On the k8s backend, start the runner nodes while a role works, so the gate after it finds them warm (placeholder
# pods; the gate removes them; they end by themselves after SDLC_K8S_WARM_TTL). Best effort, in the background.
warm_nodes() {
  [ "${SDLC_SANDBOX_BACKEND_CODE:-}" = k8s ] || return 0
  mkdir -p "$RUN/logs"
  python3 "$SKILL_DIR/scripts/k8s/run.py" --warm >> "$RUN/logs/k8s-warm.log" 2>&1 9>&- &
}
# imp WS DSTROOT REL...: copy files a role wrote; symlinks and non-regular files are refused and logged.
imp() {
  local ws="$1" dst="$2"; shift 2; mkdir -p "$RUN/logs"
  "${TOOL[@]}" import "$ws" "$dst" "$@" > "$RUN/logs/import.json" 2>&1 && return 0
  echo "$(date +%FT%T) refused import from a role workspace: $(cat "$RUN/logs/import.json")" >> "$RUN/violations.log"
  return 1
}
imp_tree() { "${TOOL[@]}" import-tree "$1" "$2" "$3" > /dev/null 2>&1 || true; }
# sbx_code DIR -- CMD: run code under test in DIR: read-only inputs, writable generated output, loopback only.
sbx_code() {
  local d="$1"; shift; [ "$1" = -- ] && shift
  local args=(--ro "$d" --net local --deny "$PROJECT") g
  for g in $GATE_GENERATED_PATHS; do mkdir -p "$d/$g"; args+=(--rw "$d/$g"); done
  "$SBX" "${args[@]}" -- "$@"
}
is_docker() { [ -n "${SBX_KIND:-}" ] || SBX_KIND="$("$SBX" --probe)"; [ "$SBX_KIND" = docker ]; }  # probed once
# Docker: gate steps and repros are offline, so the volume must hold this project's locked dependencies.
docker_warm() { if is_docker; then warmup; fi; }
# Fill the read-only dependency cache that sandboxed builds use, from the skeleton (build files and
# wrapper) of SRC (default: the project). No role-written code executes here. sandbox-exec: runs
# unsandboxed and fills ~/.gradle. Docker: scripts/docker/warmup.sh fills the cache volume, in a
# container that sees only the skeleton copy.
warmup() {
  [ -n "${GATE_WARMUP_CMD:-}" ] || return 0
  local src="${1:-$PROJECT}" w; w="$(new_ws warmup)"; mkdir -p "$RUN/logs"
  # shellcheck disable=SC2086
  "${TOOL[@]}" copy "$src" "$w" $GATE_SKELETON_PATHS > /dev/null
  if is_docker; then
    # Skip a skeleton already warmed into this cache volume (same files, same images).
    local key; key="$("${TOOL[@]}" fingerprint "$w") $(python3 "$SKILL_DIR/scripts/docker/run.py" --ids | tr '\n' ' ')"
    if ! grep -qxF "$key" "$RUN/.docker-warmed" 2>/dev/null; then
      if "$SKILL_DIR/scripts/docker/warmup.sh" "$w" "$GATE_WARMUP_CMD" >> "$RUN/logs/warmup.log" 2>&1; then
        echo "$key" >> "$RUN/.docker-warmed"
      else
        say "dependency warm-up failed (offline builds will miss dependencies): .sdlc/logs/warmup.log"
      fi
    fi
  else
    (cd "$w" && bash -c "$GATE_WARMUP_CMD") > "$RUN/logs/warmup.log" 2>&1 || say "dependency warm-up failed (sandboxed builds will download instead): .sdlc/logs/warmup.log"
  fi
  rm -rf "$w"
}

# ------------------------------------------------------------------ commands

cmd_init() {
  local scaffold="" pkg=""
  while [ $# -gt 0 ]; do
    case "$1" in --scaffold) scaffold="$2"; shift 2 ;; --package) pkg="$2"; shift 2 ;; *) die "init: $1?" ;; esac
  done
  mkdir -p "$RUN"
  [ -f "$PROJECT/gate.config" ] || sed -e "s/__PACKAGE__/${pkg:-CHANGE_ME}/g" "$SKILL_DIR/templates/gate.config.kotlin" > "$PROJECT/gate.config"
  [ -f "$PROJECT/sdlc.config" ] || cp "$SKILL_DIR/templates/sdlc.config" "$PROJECT/sdlc.config"
  if [ "$scaffold" = kotlin ]; then
    [ -n "$pkg" ] || die "init --scaffold kotlin needs --package PKG"
    for f in build.gradle.kts settings.gradle.kts detekt.yml gradle.properties; do
      [ -f "$PROJECT/$f" ] || sed -e "s/__PACKAGE__/$pkg/g" -e "s/__NAME__/$(basename "$PROJECT")/g" \
        "$SKILL_DIR/templates/kotlin/$f" > "$PROJECT/$f"
    done
    mkdir -p "$PROJECT/src/main/kotlin" "$PROJECT/src/test/kotlin"
    # The per-test Kotest plugin for Pitest (locked with the build files).
    [ -d "$PROJECT/src/sdlcPitest" ] || cp -R "$SKILL_DIR/templates/kotlin/sdlcPitest" "$PROJECT/src/sdlcPitest"
    # The template this project starts from: the base of a later `upgrade` (three-way merge).
    mkdir -p "$PROJECT/.sdlc/template-base"
    sed -e "s/__PACKAGE__/$pkg/g" -e "s/__NAME__/$(basename "$PROJECT")/g" \
      "$SKILL_DIR/templates/kotlin/build.gradle.kts" > "$PROJECT/.sdlc/template-base/build.gradle.kts"
    if [ ! -x "$PROJECT/gradlew" ]; then
      # shellcheck disable=SC1091
      (cd "$PROJECT" && . ./gate.config && [ -z "${GATE_CONFIG_ERROR:-}" ] \
        && gradle wrapper -q --gradle-version "${SDLC_GRADLE_VERSION:-8.10}") \
        || die "could not create the Gradle wrapper (is gradle installed? see GATE_CONFIG_ERROR in gate.config)"
    fi
  fi
  say "initialised $PROJECT (edit gate.config and sdlc.config, then: sdlc.sh spec REQUIREMENTS.md)"
}

cmd_spec() {
  load_config
  local req="${1:-}"; [ -f "$req" ] || die "spec: give a requirements file"
  if [ -d "$RUN" ] && [ -n "$(ls -A "$RUN" | grep -v -e '^archive$' -e '^\.mutex$' || true)" ]; then
    local arc; arc="$RUN/archive/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$arc"
    for f in "$RUN"/* "$RUN"/.[!.]*; do
      case "$(basename "$f")" in archive|.mutex|'.[!.]*'|'*') continue ;; esac
      [ -e "$f" ] && mv "$f" "$arc/"
    done
    say "earlier run archived in ${arc#"$PROJECT"/}: a new spec invalidates every downstream phase"
  fi
  cp "$req" "$RUN/requirements.md"
  local ws; ws="$(new_ws spec)"
  # shellcheck disable=SC2086
  copy_in "$ws" $GATE_SKELETON_PATHS $GATE_MAIN_PATHS $GATE_TEST_PATHS
  cp "$RUN/requirements.md" "$ws/REQUIREMENTS.md"; cp "$SKILL_DIR/templates/spec.md" "$ws/spec.template.md"
  printf 'Phase: spec.\nRequirements: REQUIREMENTS.md\nTemplate: spec.template.md\nExisting code (read only): this directory\nWrite: spec.md (and nothing else).\n' > "$ws/TASK.md"
  role spec "$ws" "$ws/TASK.md" || { rm -rf "$ws"; die "spec role exited non-zero; its output is not used (see .sdlc/logs/)"; }
  [ -s "$ws/spec.md" ] || { rm -rf "$ws"; die "spec role produced no spec.md (see .sdlc/logs/)"; }
  imp "$ws" "$RUN" spec.md || { rm -rf "$ws"; die "spec.md is not a regular file; refused"; }
  rm -rf "$ws"
  cp "$RUN/spec.md" "$RUN/spec.role.md"  # the role's output as written; approve diffs human edits against it
  "${TOOL[@]}" write-record --out "$RUN/spec.done" --kind spec --spec-sha "$(spec_sha)" \
    --files "$RUN/spec.md" "$RUN/spec.role.md" "$RUN/requirements.md" "$(ls -t "$RUN"/logs/spec-*.log | head -1)"
  say "STOP. Review .sdlc/spec.md (edit it if needed). Then a human approves: sdlc.sh approve"
  exit 7
}

cmd_approve() {
  [ -f "$RUN/spec.md" ] || die "no spec to approve"
  echo "spec.md sha256 $(spec_sha)"
  human_ok "approve this spec" "$@"
  write_human_record "$RUN/spec.approved" "$(spec_sha)"
  "${TOOL[@]}" spec-edit --run "$RUN" --how "$HOW" --note "${NOTE:-}"
  say "spec approved ($HOW)"
}

tests_attempt() {  # $1 = attempt number; $2 = previous workspace (or ""); prints the new workspace
  local n="$1" prev="$2" ws; ws="$(new_ws tests)"
  # shellcheck disable=SC2086
  copy_in "$ws" $GATE_SKELETON_PATHS
  cp "$RUN/spec.md" "$ws/SPEC.md"
  "${TOOL[@]}" ws-list "$ws" "$RUN/tests-ws-before.json"
  if [ -n "$prev" ]; then  # the role's own earlier output, plus what was wrong with it
    for p in $GATE_TEST_PATHS $GATE_MAIN_PATHS; do imp_tree "$prev" "$p" "$ws"; done
    for p in TESTS.md TEST_DEPENDENCIES.txt; do [ -f "$prev/$p" ] && { imp "$prev" "$ws" "$p" || true; }; done
    cp "$RUN/tests-feedback.md" "$ws/FEEDBACK.md"
  fi
  cat > "$ws/TASK.md" <<EOF
Phase: tests. Attempt $n of $SDLC_TESTS_ATTEMPTS.$( [ -n "$prev" ] && printf '\nFix the problems in FEEDBACK.md.')
Spec: SPEC.md
Write tests under: $GATE_TEST_PATHS (not under $GATE_REPRO_DIR)
Write the inventory: $GATE_TEST_INVENTORY
Interface stubs (TODO() bodies only, discarded afterwards): $GATE_MAIN_PATHS
Test-only dependencies, one group:artifact:version per line: TEST_DEPENDENCIES.txt
Compile and run against the stubs: $GATE_TEST_CMD
EOF
  role tests "$ws" "$ws/TASK.md" >&2 || say "tests role exited non-zero" >&2
  echo "$ws"
}

tests_check() {  # $1 = workspace. Writes .sdlc/tests-feedback.md; returns 0 if acceptable.
  local ws="$1" ch fb="$RUN/tests-feedback.md" bad=0
  : > "$fb"
  ch="$("${TOOL[@]}" ws-changes "$RUN/tests-ws-before.json" "$ws")"
  echo "$ch" > "$RUN/tests-ws-changes.json"
  python3 - "$ch" "$GATE_TEST_PATHS" "$GATE_MAIN_PATHS" "$GATE_REPRO_DIR" "$RUN/tests-ws-classified.json" <<'EOF'
import json, sys
ch, tests, mains, repro, out = json.loads(sys.argv[1]), sys.argv[2].split(), sys.argv[3].split(), sys.argv[4], sys.argv[5]
under = lambda r, ps: any(r == p or r.startswith(p.rstrip("/") + "/") for p in ps)
src_ext = (".kt", ".kts", ".java", ".scala", ".groovy", ".py", ".ts", ".js", ".go", ".rs", ".rb", ".cs")
c = {"tests": [], "stubs": [], "notes": [], "violations": [], "discarded": []}
for r in ch["new"] + ch["modified"]:
    if r in ("TESTS.md", "TEST_DEPENDENCIES.txt", "TASK.md", "FEEDBACK.md"): c["notes"].append(r)
    elif under(r, [repro]): c["violations"].append(f"{r}: the repro dir belongs to the attacker")
    elif under(r, tests): c["tests"].append(r)
    elif under(r, mains): c["stubs"].append(r)
    elif r.endswith(src_ext) and r not in ch["modified"]: c["violations"].append(f"{r}: source code outside the test and stub paths")
    else: c["discarded"].append(r)
json.dump(c, open(out, "w"), indent=2)
EOF
  local cls="$RUN/tests-ws-classified.json"
  if [ "$(j "$cls" 'len(d["violations"])')" != 0 ]; then
    j "$cls" '"\n".join("- " + v for v in d["violations"])' >> "$fb"; bad=1
  fi
  if [ "$(j "$cls" 'len(d["discarded"])')" != 0 ]; then
    echo "$(date +%FT%T) tests role wrote outside its outputs (discarded): $(j "$cls" 'd["discarded"]')" >> "$RUN/violations.log"
  fi
  if [ "$(j "$cls" 'len(d["tests"])')" = 0 ]; then echo "- no test files written under $GATE_TEST_PATHS" >> "$fb"; bad=1; fi
  local stubs; stubs="$(j "$cls" '" ".join(d["stubs"])')"
  # shellcheck disable=SC2086
  if [ -n "$stubs" ] && ! "${TOOL[@]}" stub-check "$ws" $stubs > "$RUN/stub-check.json"; then
    echo "- stubs under $GATE_MAIN_PATHS contain implementation; bodies must be TODO() only: $(j "$RUN/stub-check.json" 'd["violations"][:5]')" >> "$fb"; bad=1
  fi
  [ -f "$ws/$GATE_TEST_INVENTORY" ] || { echo "- no inventory at $GATE_TEST_INVENTORY" >> "$fb"; bad=1; }
  # Run the tests against the stubs in a fresh workspace the evaluator owns: the original skeleton,
  # plus only the role's test files and checked stubs. Its own build-file edits never execute here.
  local ev started f; ev="$(new_ws tests-eval)"
  # shellcheck disable=SC2086
  copy_in "$ev" $GATE_SKELETON_PATHS
  # shellcheck disable=SC2046
  imp "$ws" "$ev" $(j "$cls" '" ".join(d["tests"] + d["stubs"])') \
    || { echo "- refused: a test or stub file is a symlink or not a regular file" >> "$fb"; bad=1; }
  [ -s "$ws/TEST_DEPENDENCIES.txt" ] && "${TOOL[@]}" add-test-deps --build "$ev/$SDLC_TEST_DEP_FILE" \
    --deps "$ws/TEST_DEPENDENCIES.txt" --template "$SDLC_TEST_DEP_LINE" > /dev/null 2>> "$fb"
  # The stub run is offline: on Docker, fetch the role's test dependencies into the cache first.
  [ -s "$ws/TEST_DEPENDENCIES.txt" ] && is_docker && warmup "$ev"
  started="$(python3 -c 'import time; print(time.time())')"
  (cd "$ev" && sbx_code "$ev" -- bash -c "$GATE_TEST_CMD") 2>&1 | cat > "$RUN/logs/tests-stub-run.log" || true
  if ! "${TOOL[@]}" inventory-check --inventory "$ev/$GATE_TEST_INVENTORY" --spec "$RUN/spec.md" \
       --out "$RUN/inventory-check.json" --junit-dir "$ev/$GATE_JUNIT_DIR" --since "$started" > /dev/null 2>&1; then
    [ -f "$RUN/inventory-check.json" ] && j "$RUN/inventory-check.json" '"\n".join("- " + e for e in d["errors"])' >> "$fb"
    echo "- (test run log: tail below)" >> "$fb"; tail -25 "$RUN/logs/tests-stub-run.log" | sed 's/^/    /' >> "$fb"
    bad=1
  fi
  rm -rf "$ev"
  return $bad
}

tests_run_attempts() {  # [SEED_DIR] -> sets TESTS_WS to an accepted workspace, or exits 6
  local ws="${1:-}" n seed="${1:-}"; TESTS_WS=""
  for n in $(seq 1 "$SDLC_TESTS_ATTEMPTS"); do
    local prev="$ws"
    ws="$(tests_attempt "$n" "$prev")"
    [ -n "$prev" ] && [ "$prev" != "$seed" ] && rm -rf "$prev"
    [ -n "$seed" ] && { rm -rf "$seed"; seed=""; }
    if tests_check "$ws"; then TESTS_WS="$ws"; return 0; fi
    say "tests attempt $n rejected: $(head -3 "$RUN/tests-feedback.md" | tr '\n' ' ')"
  done
  rm -rf "$ws"; say "tests phase rejected after $SDLC_TESTS_ATTEMPTS attempts; see .sdlc/tests-feedback.md"; exit 6
}

import_tests() {  # $1 = accepted workspace: replace the project's tests (repro tests excepted) and relock
  local ws="$1" reason="$2" t
  for t in $GATE_TEST_PATHS; do
    [ -d "$PROJECT/$t" ] && find "$PROJECT/$t" -type f ! -path "$PROJECT/$GATE_REPRO_DIR/*" -delete
  done
  # shellcheck disable=SC2046
  imp "$ws" "$PROJECT" $(j "$RUN/tests-ws-classified.json" '" ".join(d["tests"])') || die "test import refused"
  if [ -s "$ws/TEST_DEPENDENCIES.txt" ]; then
    imp "$ws" "$RUN" TEST_DEPENDENCIES.txt || die "TEST_DEPENDENCIES.txt refused"
    "${TOOL[@]}" add-test-deps --build "$PROJECT/$SDLC_TEST_DEP_FILE" --deps "$RUN/TEST_DEPENDENCIES.txt" \
      --template "$SDLC_TEST_DEP_LINE" || die "test dependencies rejected"
  fi
  : > "$RUN/tests-notes.md"; [ -f "$ws/TESTS.md" ] && { imp "$ws" "$RUN" TESTS.md && mv "$RUN/TESTS.md" "$RUN/tests-notes.md"; }
  "${TOOL[@]}" spec-gaps --tests-md "$RUN/tests-notes.md" --inventory-check "$RUN/inventory-check.json" --out "$RUN/spec-gaps.md" > /dev/null
  # A human's acceptance stays valid only for exactly the gaps they accepted.
  if [ -f "$RUN/spec-gaps.accepted" ] && [ "$(j "$RUN/spec-gaps.accepted" 'd["sha256"]')" != "$(sha "$RUN/spec-gaps.md")" ]; then
    rm -f "$RUN/spec-gaps.accepted"
  fi
  "${TOOL[@]}" lock "$PROJECT" "$RUN/tests.lock" --spec-sha "$(spec_sha)" --reason "$reason" > /dev/null
  "${TOOL[@]}" write-record --out "$RUN/tests.done" --kind tests --spec-sha "$(spec_sha)" --lock-sha "$(lock_sha)" \
    --files "$RUN/inventory-check.json" "$RUN/tests-notes.md" "$PROJECT/$GATE_TEST_INVENTORY"
}

cmd_tests() {
  load_config; require_approval
  warmup
  tests_run_attempts
  import_tests "$TESTS_WS" tests; rm -rf "$TESTS_WS"
  warmup
  say "tests written, checked against stubs, and locked ($(j "$RUN/inventory-check.json" 'd["cases"]') inventory cases)"
  if gaps_open; then
    say "STOP: $(grep -c . "$RUN/spec-gaps.md") spec gap(s) in .sdlc/spec-gaps.md. Amend the spec (spec/approve/tests again), or a human runs: sdlc.sh accept-gaps"
    exit 7
  fi
}

cmd_repair_tests() {  # the dispute stays open until a human accepts the repaired tests
  load_config; require_approval; require_tests
  [ -s "$RUN/disputes.md" ] || die "no open dispute to repair"
  cat "$RUN/disputes.md"
  human_ok "uphold these disputes and send the tests back to a fresh tests role" "$@"
  local seed; seed="$(new_ws tests-seed)"
  # shellcheck disable=SC2086
  "${TOOL[@]}" copy "$PROJECT" "$seed" $GATE_TEST_PATHS > /dev/null
  rm -rf "${seed:?}/$GATE_REPRO_DIR"
  [ -s "$RUN/tests-notes.md" ] && cp "$RUN/tests-notes.md" "$seed/TESTS.md"
  { echo "A human upheld these disputes against your tests. Fix only the disputed tests; keep every other test and its inventory entry unchanged."
    echo; cat "$RUN/disputes.md"; } > "$RUN/tests-feedback.md"
  tests_run_attempts "$seed"
  rm -rf "$RUN/repair"; mkdir -p "$RUN/repair"
  local t; for t in $GATE_TEST_PATHS; do imp_tree "$TESTS_WS" "$t" "$RUN/repair/staged"; done
  rm -rf "${RUN:?}/repair/staged/$GATE_REPRO_DIR"
  for p in TESTS.md TEST_DEPENDENCIES.txt; do [ -f "$TESTS_WS/$p" ] && { imp "$TESTS_WS" "$RUN/repair/staged" "$p" || true; }; done
  cp "$RUN/tests-ws-classified.json" "$RUN/repair/classified.json"
  rm -rf "$TESTS_WS"
  python3 - "$PROJECT" "$RUN" "$GATE_TEST_INVENTORY" "$GATE_TEST_PATHS" "$GATE_REPRO_DIR" <<'PY' || { say "repair rejected (see .sdlc/repair/review.md); the dispute stays open"; exit 6; }
import hashlib, json, os, sys
project, run, inv, tests, repro = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4].split(), sys.argv[5]
staged = os.path.join(run, "repair", "staged")   # mirrors the project layout
def files(base):
    out = {}
    for t in tests:
        for dp, _, fs in os.walk(os.path.join(base, t)):
            for f in fs:
                p = os.path.join(dp, f)
                rel = os.path.relpath(p, base)
                if not rel.startswith(repro):
                    out[rel] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out
old, new = files(project), files(staged)
changed = sorted(r for r in new if r in old and old[r] != new[r])
added = sorted(r for r in new if r not in old)
removed = sorted(r for r in old if r not in new)
def cases(path):
    try:
        return {(c["class"], c["name"]) for c in json.load(open(path))["cases"] if c.get("kind") != "repro"}
    except (OSError, ValueError, KeyError):
        return set()
oldc = cases(os.path.join(project, inv))
newc = cases(os.path.join(staged, inv))
gone = sorted(oldc - newc)
disputes = open(os.path.join(run, "disputes.md")).read()
unmentioned = [c for c in gone if c[1] not in disputes and c[1].split(" ")[0] not in disputes]
lines = ["# Test repair: review before it is locked", "", "Disputes:", "", disputes.strip(), "",
         f"Changed files ({len(changed)}):"] + [f"- {r}" for r in changed] + [f"Added files ({len(added)}):"] + \
        [f"- {r}" for r in added] + [f"Removed files ({len(removed)}):"] + [f"- {r}" for r in removed] + \
        [f"Inventory cases removed or renamed ({len(gone)}):"] + [f"- {c[0]} › {c[1]}" for c in gone]
if unmentioned:
    lines += ["", "REJECTED: these cases were removed or renamed although no dispute names them:"] + \
             [f"- {c[0]} › {c[1]}" for c in unmentioned]
open(os.path.join(run, "repair", "review.md"), "w").write("\n".join(lines) + "\n")
sys.exit(1 if unmentioned else 0)
PY
  cat "$RUN/repair/review.md"
  say "STOP: review .sdlc/repair/review.md. If the repair only fixes what was disputed, a human runs: sdlc.sh accept-repair"
  exit 7
}

cmd_accept_repair() {
  load_config; require_approval
  [ -d "$RUN/repair/staged" ] || die "no staged repair"
  cat "$RUN/repair/review.md"
  human_ok "accept this test repair and lock it" "$@"
  cp "$RUN/repair/classified.json" "$RUN/tests-ws-classified.json"
  local ws t; ws="$(new_ws repair-import)"
  for t in $GATE_TEST_PATHS; do imp_tree "$RUN/repair/staged" "$t" "$ws"; done
  for t in TESTS.md TEST_DEPENDENCIES.txt; do [ -f "$RUN/repair/staged/$t" ] && cp "$RUN/repair/staged/$t" "$ws/$t"; done
  import_tests "$ws" "repair accepted ($HOW)"; rm -rf "$ws"
  mkdir -p "$RUN/disputes.resolved"; local f; f="$RUN/disputes.resolved/$(date +%Y%m%d-%H%M%S)-repair.md"
  { cat "$RUN/disputes.md"; printf '\nRuling (%s): upheld; repaired by a fresh tests role; repair accepted. %s\n' "$HOW" "${NOTE:-}"; cat "$RUN/repair/review.md"; } > "$f"
  : > "$RUN/disputes.md"; mv "$RUN/repair" "$RUN/repair.accepted-$(date +%Y%m%d-%H%M%S)"
  say "repair accepted and locked; dispute archived in ${f#"$PROJECT"/}"
  if gaps_open; then say "STOP: spec gaps changed; a human must accept them again: sdlc.sh accept-gaps"; exit 7; fi
}

cmd_accept_gaps() {
  [ -s "$RUN/spec-gaps.md" ] || die "no spec gaps to accept"
  cat "$RUN/spec-gaps.md"
  human_ok "accept these spec gaps as known limits" "$@"
  write_human_record "$RUN/spec-gaps.accepted" "$(sha "$RUN/spec-gaps.md")"
  say "spec gaps accepted ($HOW); they stay listed as not verified in the report"
}

cmd_design() {
  load_config; require_approval
  mkdir -p "$RUN/design"
  local ws jws; ws="$(new_ws design)"
  # shellcheck disable=SC2086
  copy_in "$ws/project" $GATE_SKELETON_PATHS $GATE_MAIN_PATHS
  cp "$RUN/spec.md" "$ws/SPEC.md"
  printf 'Phase: design.\nSpec: SPEC.md\nExisting code (read only): project/\nWrite: design/candidate-1.md .. candidate-%s.md\n' "$SDLC_DESIGN_CANDIDATES" > "$ws/TASK.md"
  role design "$ws" "$ws/TASK.md" || { rm -rf "$ws"; die "design role exited non-zero; its output is not used"; }
  jws="$(new_ws judge)"
  cp "$RUN/spec.md" "$jws/SPEC.md"; mkdir -p "$jws/design"
  # shellcheck disable=SC2046
  imp "$ws" "$jws" $(cd "$ws" && ls design/candidate-*.md 2>/dev/null) || { rm -rf "$ws" "$jws"; die "design output refused"; }
  python3 -c 'import hashlib,json,pathlib,sys; d=pathlib.Path(sys.argv[1]); json.dump({p.stem: hashlib.sha256(p.read_bytes()).hexdigest() for p in d.glob("candidate-*.md")}, open(sys.argv[2],"w"))' \
    "$jws/design" "$RUN/design/candidates.sha.json"
  cp "$SKILL_DIR/templates/decision.md" "$jws/decision.template.md"
  printf 'Phase: judge.\nDate: %s\nSpec: SPEC.md\nCandidates: design/candidate-*.md\nWrite: design/scores.json and design/decision.md (template: decision.template.md)\n' "$(date +%F)" > "$jws/TASK.md"
  role judge "$jws" "$jws/TASK.md" || { rm -rf "$ws" "$jws"; die "judge role exited non-zero; its output is not used"; }
  "${TOOL[@]}" judge-check --dir "$jws/design" --spec "$RUN/spec.md" --originals "$RUN/design/candidates.sha.json" > "$RUN/design/judge-check.json" \
    || { rm -rf "$ws" "$jws"; die "design phase output rejected: $(cat "$RUN/design/judge-check.json")"; }
  # shellcheck disable=SC2046
  imp "$jws" "$RUN" $(cd "$jws" && ls design/candidate-*.md) design/scores.json design/decision.md || { rm -rf "$ws" "$jws"; die "judge output refused"; }
  "${TOOL[@]}" write-record --out "$RUN/design/design.done" --kind design --spec-sha "$(spec_sha)" \
    --files "$RUN/design/decision.md" "$RUN/design/scores.json"
  rm -rf "$ws" "$jws"
  say "design chosen: .sdlc/design/decision.md"
}

design_valid() { "${TOOL[@]}" record-check --record "$RUN/design/design.done" --spec-sha "$(spec_sha)" > /dev/null 2>&1; }

# gate_build LABEL: the build loop's gate. With SDLC_MUTATION_HISTORY=1, Pitest reuses the history of
# earlier gates, which speeds up failing iterations. A pass that reused history is never final: a fresh
# run (history written, not read) confirms it, and only that run's report counts.
gate_build() {
  local label="$1" hargs=() g
  [ "${SDLC_MUTATION_HISTORY:-1}" = 1 ] && hargs=(--mutation-history "$RUN/pitest-history")
  "$SKILL_DIR/scripts/gate.sh" --project "$PROJECT" --label "$label" "${hargs[@]+"${hargs[@]}"}" || return 1
  g="$(ls -td "$RUN"/gate/*-"$label"-* | head -1)"
  if [ "$(j "$g/gate-report.json" 'd.get("mutation_incremental", False)')" = True ]; then
    say "gate passed with incremental mutation analysis; confirming with a fresh run"
    "$SKILL_DIR/scripts/gate.sh" --project "$PROJECT" --label "$label-confirm" "${hargs[@]+"${hargs[@]}"}" --fresh || return 1
  fi
}
# mutation_timed_out GATE_DIR: that gate's mutation step was killed at GATE_STEP_TIMEOUT.
mutation_timed_out() { [ "$(j "$1/gate-report.json" '"mutation" in d.get("timed_out", [])' 2>/dev/null)" = True ]; }
# infra_stop GATE_DIR: a time limit is not a defect the builder can see, so it is not called again.
infra_stop() {
  : > "$RUN/build/infra-stop"
  say "STOP: the gate's mutation step timed out (GATE_MUTATION_TIMEOUT or GATE_STEP_TIMEOUT). That is a time limit, not a code defect, so the"
  say "builder is not called again. A human decides: make Pitest faster (sdlc.pitestExcludedTests, iterations in"
  say "sdlc.pitestJvmArgs, sdlc.pitestThreads, SDLC_DOCKER_CPUS) or raise the limit, relock, and rerun."
  say "The rerun gates the current code before it calls the builder. Report: ${1#"$PROJECT"/}/gate-report.md"
}
main_files() { local m; for m in $GATE_MAIN_PATHS; do find "$PROJECT/$m" -type f 2>/dev/null; done | head -1; }

cmd_build() {
  load_config; require_approval; require_tests; require_no_gaps
  docker_warm
  local round=0; [ "${1:-}" = "--round" ] && round="$2"
  export SDLC_ROUND="$round"
  mkdir -p "$RUN/build"; touch "$RUN/build/notes.md" "$RUN/disputes.md"
  local i ws last_gate="" dsha
  # Resuming after an infrastructure stop: the code did not get a verdict, so gate it before the builder runs.
  if [ -f "$RUN/build/infra-stop" ]; then
    rm -f "$RUN/build/infra-stop"
    if [ -n "$(main_files)" ]; then
      say "resuming after an infrastructure stop: gating the current code before calling the builder"
      if gate_build "build-r$round-i0"; then say "gate PASSED (round $round, current code)"; return 0; fi
      last_gate="$(ls -td "$RUN"/gate/*-build-r"$round"-i0-* | head -1)"
      if mutation_timed_out "$last_gate"; then infra_stop "$last_gate"; return 4; fi
    fi
  fi
  for i in $(seq 1 "$SDLC_BUILD_MAX_ITERS"); do
    ws="$(new_ws build)"
    # The whole locked set plus main code (docs excluded), so the builder's scratch gate sees what
    # the real gate sees. Orchestrator inputs live in .sdlc-in/, which no file set counts.
    "${TOOL[@]}" copy "$PROJECT" "$ws" --no-docs > /dev/null
    mkdir -p "$ws/.sdlc" "$ws/.sdlc-in"; cp "$RUN/tests.lock" "$ws/.sdlc/tests.lock"
    cp "$RUN/spec.md" "$ws/.sdlc-in/SPEC.md"
    cp "$RUN/build/notes.md" "$ws/.sdlc-in/notes.md"; cp "$RUN/disputes.md" "$ws/.sdlc-in/disputes.md"
    [ -f "$RUN/tests-notes.md" ] && cp "$RUN/tests-notes.md" "$ws/.sdlc-in/TESTS.md"
    design_valid && cp "$RUN/design/decision.md" "$ws/.sdlc-in/design-decision.md"
    [ "$round" -gt 0 ] && j "$RUN/attack/round-$round/findings.validated.json" \
      'json.dumps([f for f in d["findings"] if f["status"]=="reproduced"], indent=2)' 2>/dev/null > "$ws/.sdlc-in/findings-to-fix.json" || true
    [ -n "$last_gate" ] && cp -R "$last_gate" "$ws/.sdlc-in/last-gate"
    cat > "$ws/.sdlc-in/TASK.md" <<EOF
Phase: build. Round $round, iteration $i of $SDLC_BUILD_MAX_ITERS.
Spec: .sdlc-in/SPEC.md
Locked tests: $GATE_TEST_PATHS (inventory: $GATE_TEST_INVENTORY)
Your code: $GATE_MAIN_PATHS. Nothing else you change is kept.
Inputs: .sdlc-in/ (TESTS.md, design-decision.md, findings-to-fix.json, last-gate/, notes.md, disputes.md, when present)
Your notes (read, then append): .sdlc-in/notes.md
Disputes (append one line per dispute, then stop): .sdlc-in/disputes.md
Check yourself: $(check_prefix build)$SKILL_DIR/scripts/gate.sh --scratch --fast   (report: .sdlc/build/gate-scratch/gate-report.md)
  Integrity, compile, tests and static analysis in a few minutes. It skips Pitest, so that report always
  fails on "mutation: not run"; every other step must pass. Do not run Pitest or the scratch gate without
  --fast: the orchestrator's gate runs mutation testing after you stop, and its surviving mutants come back
  to you in .sdlc-in/last-gate/gate-report.md.
Time limit for this session: ${SDLC_ROLE_TIMEOUT:-3600}s. Write your notes before it runs out.
EOF
    dsha="$(sha "$RUN/disputes.md")"
    warm_nodes
    role build "$ws" "$ws/.sdlc-in/TASK.md" || say "builder exited non-zero"
    # Import only main paths, notes and disputes. Record (but discard) anything else the builder changed.
    if ! "${TOOL[@]}" verify-lock "$ws" "$RUN/tests.lock" > "$RUN/build/ws-lock-r$round-i$i.json" 2>&1; then
      echo "$(date +%FT%T) builder (round $round, iteration $i) changed locked files in its workspace (discarded): $(j "$RUN/build/ws-lock-r$round-i$i.json" '{k: d[k] for k in ("changed","missing","added")}' 2>/dev/null)" >> "$RUN/violations.log"
    fi
    for m in $GATE_MAIN_PATHS; do rm -rf "${PROJECT:?}/$m"; [ -e "$ws/$m" ] && "${TOOL[@]}" copy "$ws" "$PROJECT" "$m" > /dev/null; done
    imp "$ws/.sdlc-in" "$RUN/build" notes.md || true
    imp "$ws/.sdlc-in" "$RUN" disputes.md || true
    rm -rf "$ws"
    if [ "$(sha "$RUN/disputes.md")" != "$dsha" ] && [ -s "$RUN/disputes.md" ]; then
      say "builder filed a dispute (.sdlc/disputes.md). A human must rule; stopping."; return 3
    fi
    if gate_build "build-r$round-i$i"; then
      say "gate PASSED (round $round, iteration $i)"; return 0
    fi
    last_gate="$(ls -td "$RUN"/gate/*-build-r"$round"-i"$i"-* | head -1)"
    if mutation_timed_out "$last_gate"; then infra_stop "$last_gate"; return 4; fi
  done
  say "gate still failing after $SDLC_BUILD_MAX_ITERS builder iterations (round $round)"; return 4
}

attack_ws() {  # $1 workspace, $2 round, $3 source dir, $4 "no-tests" to hide tests, $5 sub-package, $6 id prefix
  local ws="$1" round="$2" src="$3" sub="${5:-}" prefix="${6:-}" paths=()
  # shellcheck disable=SC2206
  paths=($GATE_SKELETON_PATHS $GATE_MAIN_PATHS); [ "${4:-}" = no-tests ] || paths+=($GATE_TEST_PATHS)
  "${TOOL[@]}" copy "$src" "$ws" "${paths[@]}" > /dev/null
  cp "$RUN/spec.md" "$ws/SPEC.md"
  local rdir="$GATE_REPRO_DIR/round$round${sub:+/$sub}" pkg="attack.round$round${sub:+.$sub}"
  mkdir -p "$ws/attack" "$ws/$rdir"
  cp "$SKILL_DIR/templates/findings.schema.json" "$ws/attack/findings.schema.json"
  gated_list > "$ws/attack/GATED.md"; [ -s "$ws/attack/GATED.md" ] || rm -f "$ws/attack/GATED.md"
  local runcmd="${GATE_REPRO_CMD//\{tests\}/--tests \"$pkg.*\"}"
  cat > "$ws/attack/TASK.md" <<EOF
Phase: attack. Round $round.
Spec: SPEC.md
Code$( [ "${4:-}" = no-tests ] || printf ' and tests'): this directory.
Write repro tests (one self-contained file per finding) in: $rdir/  (package $pkg)$( [ -n "$prefix" ] && printf '\nFinding ids: start every id with %s (e.g. %sF1); each test name starts with its id, as usual.' "$prefix" "$prefix")
Write findings to: attack/findings.json (schema: attack/findings.schema.json)
Run your repros: $(check_prefix attack)$runcmd$( [ -f "$ws/attack/GATED.md" ] && printf '\nAlready checked mechanically by the gate on every build (do not spend effort here): attack/GATED.md')
EOF
}

# attackers: the models of one attack round (SDLC_ATTACKERS, space-separated; default: SDLC_MODEL_attack, one attacker).
attackers() { printf '%s' "${SDLC_ATTACKERS:-${SDLC_MODEL_attack:-}}"; }

# run_attacker ROUND SOURCE_DIR OUT_DIR [no-tests] -> sets ATTACK_WS (valid findings) or returns 5.
# With several attackers (speed spec S4.4) they run at the same time, each in its own workspace, sub-package
# (attack.roundN.aK) and id prefix (AK), and their output is merged into one workspace. Any attacker that does not
# complete makes the round incomplete.
run_attacker() {
  local round="$1" src="$2" out="$3" hide="${4:-}" models k n rc=0 pids=()
  read -r -a models <<< "$(attackers)"; n="${#models[@]}"
  if [ "$n" -le 1 ]; then run_one_attacker "$round" "$src" "$out" "$hide" "" ""; return $?; fi
  warm_nodes
  for k in $(seq 1 "$n"); do
    mkdir -p "$out/a$k"
    ( SDLC_MODEL_attack="${models[$((k - 1))]}" run_one_attacker "$round" "$src" "$out/a$k" "$hide" "a$k" "A$k" nowarm \
        && printf '%s' "$ATTACK_WS" > "$out/a$k/ws" ) &
    pids+=("$!")
  done
  for k in $(seq 1 "$n"); do wait "${pids[$((k - 1))]}" || rc=5; done
  ATTACK_WS="$(new_ws attack)"
  local parts=()
  for k in $(seq 1 "$n"); do
    local w; w="$(cat "$out/a$k/ws" 2>/dev/null)"
    if [ -n "$w" ] && [ -d "$w" ]; then parts+=("$w"); else rc=5; fi
  done
  if [ "$rc" != 0 ]; then
    for w in "${parts[@]+"${parts[@]}"}"; do rm -rf "$w"; done; rm -rf "$ATTACK_WS"; ATTACK_WS=""
    say "attack round $round: an attacker did not complete (models: ${models[*]})"; return 5
  fi
  python3 - "$ATTACK_WS" "$GATE_REPRO_DIR/round$round" "${models[*]}" "${parts[@]}" <<'PY' || { rm -rf "$ATTACK_WS"; return 5; }
import json, os, shutil, sys
dst, rdir, models, parts = sys.argv[1], sys.argv[2], sys.argv[3].split(), sys.argv[4:]
merged = {"findings": [], "attempted": [], "attackers": models}
os.makedirs(os.path.join(dst, "attack"), exist_ok=True)
for k, ws in enumerate(parts, 1):
    d = json.load(open(os.path.join(ws, "attack", "findings.json")))
    merged["findings"] += d.get("findings", [])
    merged["attempted"] += [f"a{k}: {x}" for x in d.get("attempted", [])]
    src = os.path.join(ws, rdir, f"a{k}")
    if os.path.isdir(src):
        shutil.copytree(src, os.path.join(dst, rdir, f"a{k}"), symlinks=True)
shutil.copy(os.path.join(parts[0], "attack", "findings.schema.json"), os.path.join(dst, "attack", "findings.schema.json"))
json.dump(merged, open(os.path.join(dst, "attack", "findings.json"), "w"), indent=2)
PY
  for w in "${parts[@]}"; do rm -rf "$w"; done
  "${TOOL[@]}" check-findings "$ATTACK_WS/attack/findings.json" > "$out/check-findings.json" || { rm -rf "$ATTACK_WS"; return 5; }
  return 0
}

# run_one_attacker ROUND SOURCE_DIR OUT_DIR HIDE SUB PREFIX [nowarm] -> sets ATTACK_WS or returns 5
run_one_attacker() {
  local round="$1" src="$2" out="$3" hide="${4:-}" sub="${5:-}" prefix="${6:-}" try; ATTACK_WS=""
  for try in $(seq 0 "$SDLC_ATTACK_RETRIES"); do
    [ -n "$ATTACK_WS" ] && rm -rf "$ATTACK_WS"
    ATTACK_WS="$(new_ws attack)"; attack_ws "$ATTACK_WS" "$round" "$src" "$hide" "$sub" "$prefix"
    [ "${7:-}" = nowarm ] || warm_nodes
    if role attack "$ATTACK_WS" "$ATTACK_WS/attack/TASK.md" \
       && "${TOOL[@]}" check-findings "$ATTACK_WS/attack/findings.json" > "$out/check-findings.json"; then
      return 0
    fi
    say "attacker attempt $((try + 1)) failed: $(cat "$out/check-findings.json" 2>/dev/null || echo 'non-zero exit')"
    # Back off before retrying: provider errors right after a heavy gate run are usually transient.
    [ "$try" -lt "$SDLC_ATTACK_RETRIES" ] && sleep "$(( ${SDLC_RETRY_BACKOFF:-30} * (try + 1) ))"
  done
  imp_tree "$ATTACK_WS" attack "$out/workspace-out"
  rm -rf "$ATTACK_WS"; ATTACK_WS=""
  return 5
}

cmd_attack() {
  load_config; require_approval; require_tests; require_no_gaps
  docker_warm
  [ "${1:-}" = "--round" ] || die "attack --round N"
  local round="$2" out="$RUN/attack/round-$2" code_fp lsha
  export SDLC_ROUND="$round"  # recorded with each role run (token accounting per round)
  [ -d "$out" ] && mv "$out" "$RUN/attack/superseded-round-$round-$(date +%Y%m%d-%H%M%S)"
  mkdir -p "$out"
  snapshot_main "$out/snapshot-pre" || say "attack round $round: could not snapshot the main sources (consolidation will be skipped)"
  code_fp="$(fp)"
  if ! run_attacker "$round" "$PROJECT" "$out"; then
    echo "attacker failed after $((SDLC_ATTACK_RETRIES + 1)) attempts" > "$out/attacker.failed"
    say "attack round $round DID NOT COMPLETE. This is not a clean round."; return 5
  fi
  imp_tree "$ATTACK_WS" attack "$out/workspace-out"
  imp_tree "$ATTACK_WS" "$GATE_REPRO_DIR/round$round" "$out/workspace-out"
  finish_round "$round" "$out" "$ATTACK_WS" "$code_fp"
}

# finish_round ROUND OUT WORKSPACE CODE_FP: validate the attacker's findings (WORKSPACE holds attack/ and the repros),
# referee them, lock accepted repros, write the round record. Removes WORKSPACE.
finish_round() {
  local round="$1" out="$2" ATTACK_WS="$3" code_fp="$4" lsha summary
  summary="$("${TOOL[@]}" validate-findings --project "$PROJECT" --workspace "$ATTACK_WS" --findings attack/findings.json \
    --out "$out" --spec "$RUN/spec.md" --round "$round" --runs "$SDLC_REPRO_RUNS" --timeout "${SDLC_REPRO_TIMEOUT:-900}")" \
    || { rm -rf "$ATTACK_WS"; echo "validation failed" > "$out/attacker.failed"; die "finding validation failed"; }
  rm -rf "$ATTACK_WS"
  if [ "$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["incomplete"])' "$summary")" != 0 ]; then
    echo "repro validation incomplete (timeouts): not a verdict" > "$out/attacker.failed"
    say "attack round $round: repro validation incomplete; this is not a clean round"; return 5
  fi
  referee "$round" "$out" || { echo "referee did not complete" > "$out/attacker.failed"; say "attack round $round: referee failed"; return 5; }
  summary="$(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(json.dumps({**{s: sum(1 for f in d["findings"] if f["status"]==s) for s in ("reproduced","discarded","flaky","spec-gap","incomplete","rejected-by-referee")}, "accepted_files": [f["repro"] for f in d["findings"] if f["status"]=="reproduced"]}))' "$out/findings.validated.json")"
  local files; files="$(python3 -c 'import json,sys; print(" ".join(json.loads(sys.argv[1])["accepted_files"]))' "$summary")"
  if [ -n "$files" ]; then
    python3 - "$out/findings.validated.json" "$PROJECT" <<'PY' || die "could not import accepted repros"
import json, os, shutil, sys
d = json.load(open(sys.argv[1]))
for f in d["findings"]:
    if f["status"] == "reproduced":
        dst = os.path.join(sys.argv[2], f["repro"])
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.lexists(dst):
            raise SystemExit(f"refusing to overwrite {dst}")
        shutil.copyfile(f["staged"], dst)
PY
    "${TOOL[@]}" inventory-add-repros --inventory "$PROJECT/$GATE_TEST_INVENTORY" --validated "$out/findings.validated.json"
    # shellcheck disable=SC2086
    "${TOOL[@]}" lock "$PROJECT" "$RUN/tests.lock" --add $files "$GATE_TEST_INVENTORY" --reason "repro round $round" > /dev/null
  fi
  lsha="$(lock_sha)"
  "${TOOL[@]}" write-record --out "$out/attacker.done" --kind attack --fingerprint "$code_fp" --spec-sha "$(spec_sha)" \
    --lock-sha "$lsha" --attacker-cfg "$(attacker_cfg)" --summary "$summary" --files "$out/findings.validated.json"
  say "attack round $round: $summary"
}

# revalidate --round N: validate round N's saved attacker output again (after an infrastructure failure made its
# validation say nothing about the code), without a new attacker. The earlier verdict is kept, superseded. Refused if
# the code changed since the attack: then the round must be attacked again.
cmd_revalidate() {
  load_config; require_approval; require_tests; require_no_gaps
  docker_warm
  [ "${1:-}" = "--round" ] || die "revalidate --round N"
  local round="$2" out="$RUN/attack/round-$2" old ws
  [ -d "$out/workspace-out/attack" ] || die "no saved attacker output for round $round"
  if [ -f "$out/attacker.done" ] && [ "$(j "$out/attacker.done" 'd["fingerprint"]')" != "$(fp)" ]; then
    die "the code changed since round $round was attacked: attack it again (sdlc.sh attack --round $round)"
  fi
  old="$out/superseded-validation-$(date +%Y%m%d-%H%M%S)"; mkdir -p "$old"
  for f in findings.validated.json check-findings.json attacker.done attacker.failed logs referee; do
    [ -e "$out/$f" ] && mv "$out/$f" "$old/"
  done
  ws="$(new_ws revalidate)"; cp -R "$out/workspace-out/." "$ws/"
  echo "$(date +%FT%T) revalidate round $round from the attacker's saved output (earlier verdict: ${old#"$RUN"/})" >> "$RUN/violations.log"
  finish_round "$round" "$out" "$ws" "$(fp)"
}

cmd_calibrate() {
  load_config; require_approval; require_tests
  docker_warm
  local cal; cal="$RUN/calibration/$(date +%Y%m%d-%H%M%S)"; mkdir -p "$cal/logs"
  local mutants="${1:-}"
  if [ -n "$mutants" ]; then
    mutants="$(cd "$(dirname "$mutants")" && pwd -P)/$(basename "$mutants")"
    case "$mutants" in "$RUN"/*) ;; "$PROJECT"/*) die "keep MUTANTS.json outside the project or under .sdlc/ (the attacker would see it)" ;; esac
    cp "$mutants" "$cal/proposed.json"
  else  # a planter role invents the bugs; its output never enters an attacker workspace
    local pws; pws="$(new_ws plant)"
    # shellcheck disable=SC2086
    copy_in "$pws" $GATE_SKELETON_PATHS $GATE_MAIN_PATHS
    cp "$RUN/spec.md" "$pws/SPEC.md"
    printf 'Phase: plant.\nSpec: SPEC.md\nCode: %s\nWrite: mutants.json\n' "$GATE_MAIN_PATHS" > "$pws/TASK.md"
    role plant "$pws" "$pws/TASK.md" || { rm -rf "$pws"; say "planter exited non-zero; calibration skipped"; return 1; }
    imp "$pws" "$cal" mutants.json && mv "$cal/mutants.json" "$cal/proposed.json" || { rm -rf "$pws"; say "planter wrote no usable mutants.json; calibration skipped"; return 1; }
    rm -rf "$pws"
  fi
  local crc=0
  "${TOOL[@]}" check-mutants --project "$PROJECT" --mutants "$cal/proposed.json" --out "$cal/mutants.json" --logs "$cal/logs" \
    > "$cal/check-mutants.json" || crc=$?
  if [ "$crc" = 5 ]; then say "calibration incomplete: a sandboxed run failed or timed out (see ${cal#"$PROJECT"/}/logs); not a verdict"; return 5; fi
  [ "$crc" = 0 ] || { say "no usable planted bug; calibration skipped"; return 1; }
  local code_fp; code_fp="$(fp)"
  "${TOOL[@]}" copy "$PROJECT" "$cal/original" > /dev/null
  "${TOOL[@]}" copy "$PROJECT" "$cal/mutated" > /dev/null
  "${TOOL[@]}" mutate --root "$cal/mutated" --mutants "$cal/mutants.json" > /dev/null
  mkdir -p "$cal/attack"
  if ! run_attacker 0 "$cal/mutated" "$cal/attack" no-tests; then say "calibration attacker failed"; return 1; fi
  imp_tree "$ATTACK_WS" attack "$cal/attack/workspace-out"
  "${TOOL[@]}" validate-findings --project "$cal/mutated" --workspace "$ATTACK_WS" --findings attack/findings.json \
    --out "$cal/attack" --spec "$RUN/spec.md" --round 0 --runs "$SDLC_REPRO_RUNS" > /dev/null || { rm -rf "$ATTACK_WS"; return 1; }
  rm -rf "$ATTACK_WS"
  "${TOOL[@]}" calibrate-score --cal "$cal" --original "$cal/original" --mutated "$cal/mutated" --mutants "$cal/mutants.json" \
    --model "$(attackers)" --attacker-cfg "$(attacker_cfg)" --fingerprint "$code_fp" \
    || { crc=$?; [ "$crc" = 5 ] && say "calibration incomplete: a sandboxed run failed or timed out; not a verdict"; return "$crc"; }
  rm -rf "$cal/original"; find "$cal/mutated" -mindepth 1 -maxdepth 1 ! -name src -exec rm -rf {} +
  say "calibration: ${cal#"$PROJECT"/}/calibration.json"
}

referee() {  # $1 round, $2 out dir: a fresh role judges each reproduced test against the spec
  local round="$1" out="$2" n
  n="$(python3 -c 'import json,sys; print(sum(1 for f in json.load(open(sys.argv[1]))["findings"] if f["status"]=="reproduced"))' "$out/findings.validated.json")"
  [ "$n" = 0 ] && return 0
  if [ "${SDLC_REFEREE:-on}" != on ]; then echo "$(date +%FT%T) referee off: round $round repros locked unrefereed" >> "$RUN/violations.log"; return 0; fi
  local ws; ws="$(new_ws referee)"; mkdir -p "$ws/referee/tests"
  cp "$RUN/spec.md" "$ws/SPEC.md"
  python3 - "$out/findings.validated.json" "$ws/referee" <<'PY'
import json, os, shutil, sys
d = json.load(open(sys.argv[1])); out = []
for f in d["findings"]:
    if f["status"] == "reproduced":
        shutil.copyfile(f["staged"], os.path.join(sys.argv[2], "tests", os.path.basename(f["staged"])))
        out.append({k: f.get(k) for k in ("id", "title", "spec_refs", "expected", "actual", "failures")}
                   | {"test": "tests/" + os.path.basename(f["staged"])})
json.dump({"findings": out}, open(os.path.join(sys.argv[2], "findings.json"), "w"), indent=2)
PY
  printf 'Phase: referee. Round %s.
Spec: SPEC.md
Findings: referee/findings.json (tests in referee/tests/)
Write: referee/verdicts.json
' "$round" > "$ws/TASK.md"
  role referee "$ws" "$ws/TASK.md" || { rm -rf "$ws"; return 1; }
  imp "$ws" "$out" referee/verdicts.json || { rm -rf "$ws"; return 1; }
  rm -rf "$ws"
  python3 - "$out/findings.validated.json" "$out/referee/verdicts.json" <<'PY'
import json, sys
d = json.load(open(sys.argv[1])); v = {x.get("id"): x for x in json.load(open(sys.argv[2])).get("verdicts", []) if isinstance(x, dict)}
for f in d["findings"]:
    if f["status"] != "reproduced":
        continue
    x = v.get(f["id"])
    if not x or x.get("verdict") not in ("valid", "invalid", "spec-gap"):
        raise SystemExit(f"no verdict for {f['id']}")
    f["referee"] = {"verdict": x["verdict"], "reason": str(x.get("reason", ""))[:400]}
    if x["verdict"] == "invalid":
        f["status"], f["reason"] = "rejected-by-referee", f"referee: {x.get('reason', '')}"[:300]
    elif x["verdict"] == "spec-gap":
        f["status"], f["reason"] = "spec-gap", f"referee: the spec does not decide this: {x.get('reason', '')}"[:300]
json.dump(d, open(sys.argv[1], "w"), indent=2)
PY
}

# snapshot_main DEST: a copy of the main sources (file-set rules apply). The pre-fix side of consolidation.
snapshot_main() {
  local dst="$1"
  rm -rf "$dst"; mkdir -p "$dst"
  # shellcheck disable=SC2086
  "${TOOL[@]}" copy "$PROJECT" "$dst" $GATE_MAIN_PATHS > /dev/null
}

# consolidate ROUND: after round ROUND's fix passed the gate, a fresh role proposes deterministic checks for the round's
# reproduced findings; consolidate.py validates them without a model and STAGES the accepted rules (never adopts them).
# Best effort and idempotent: any failure is reported and never fails the run.
consolidate() {
  local round="$1" out="$RUN/attack/round-$1" cdir="$RUN/consolidate/round-$1" ws n
  local SDLC_ROUND="$round"; export SDLC_ROUND
  [ "${SDLC_CONSOLIDATE:-on}" = on ] || return 0
  [ -f "$out/findings.validated.json" ] || return 0
  n="$(python3 -c 'import json,sys; print(sum(1 for f in json.load(open(sys.argv[1]))["findings"] if f["status"]=="reproduced"))' "$out/findings.validated.json")"
  [ "$n" = 0 ] && return 0
  if [ -f "$cdir/report.json" ]; then say "consolidate round $round: done earlier"; return 0; fi
  [ -d "$out/snapshot-pre" ] || { say "consolidate round $round: no pre-fix snapshot for this round; skipped"; return 0; }
  mkdir -p "$cdir"
  snapshot_main "$cdir/fixed" || { say "consolidate round $round: could not snapshot the fixed sources"; return 1; }
  ws="$(new_ws consolidate)"; mkdir -p "$ws/consolidate/rules" "$ws/consolidate/tests"
  cp "$RUN/spec.md" "$ws/SPEC.md"
  cp -R "$out/snapshot-pre" "$ws/pre"; cp -R "$cdir/fixed" "$ws/fixed"
  python3 - "$out/findings.validated.json" "$ws/consolidate" <<'PY'
import json, os, shutil, sys
d = json.load(open(sys.argv[1])); out = []
for f in d["findings"]:
    if f["status"] != "reproduced":
        continue
    row = {k: f.get(k) for k in ("id", "title", "category", "severity", "description", "expected", "actual", "spec_refs")}
    row["referee"] = f.get("referee")
    staged = f.get("staged")
    if staged and os.path.isfile(staged):
        shutil.copyfile(staged, os.path.join(sys.argv[2], "tests", os.path.basename(staged)))
        row["test"] = "tests/" + os.path.basename(staged)
    out.append(row)
json.dump({"findings": out}, open(os.path.join(sys.argv[2], "findings.json"), "w"), indent=2)
PY
  printf 'Phase: consolidate. Round %s.
Spec: SPEC.md
Findings: consolidate/findings.json (tests in consolidate/tests/)
Sources before the fix: pre/; after the fix: fixed/
Write: consolidate/proposals.json and consolidate/rules/*.yml
' "$round" > "$ws/TASK.md"
  role consolidate "$ws" "$ws/TASK.md" || { rm -rf "$ws"; say "consolidate round $round: the role did not complete"; return 1; }
  imp_tree "$ws" consolidate "$cdir/role-out"
  rm -rf "$ws"
  if python3 "$SKILL_DIR/scripts/consolidate.py" --findings "$out/findings.validated.json" \
       --proposals "$cdir/role-out/consolidate/proposals.json" --rules-dir "$cdir/role-out/consolidate/rules" \
       --pre "$out/snapshot-pre" --fixed "$cdir/fixed" --out "$cdir/report.json" --stage "$cdir/staged-rules" \
       > "$cdir/summary.json" 2> "$cdir/consolidate.err"; then
    say "consolidate round $round: $(cat "$cdir/summary.json")"
  else
    say "consolidate round $round: proposals could not be validated ($(head -c 200 "$cdir/consolidate.err"))"; return 1
  fi
}
cmd_consolidate() {  # consolidate --round N: run the step for a finished fix round (also after an earlier failure)
  load_config
  [ "${1:-}" = "--round" ] || die "consolidate --round N"
  consolidate "$2"
}

gate_fresh_pass() { "${TOOL[@]}" gate-fresh --project "$PROJECT" --run "$RUN" > /dev/null; }
round_rec() {  # completed, unchanged, same spec, same attacker configuration, same harness
  [ ! -f "$RUN/attack/round-$1/attacker.failed" ] && "${TOOL[@]}" record-check --record "$RUN/attack/round-$1/attacker.done" \
    --spec-sha "$(spec_sha)" --attacker-cfg "$(attacker_cfg)" > /dev/null 2>&1
}
rec() { j "$RUN/attack/round-$1/attacker.done" "$2"; }

cmd_loop() {
  load_config; require_approval; require_tests; require_no_gaps
  local rc=0 r
  if gate_fresh_pass; then say "a fresh passing gate exists for this code, spec and lock"
  else cmd_build --round 0 || { rc=$?; say "build did not pass the gate (rc=$rc)"; return $rc; }; fi
  for r in $(seq 1 "$SDLC_MAX_ROUNDS"); do
    if round_rec "$r"; then
      if [ "$(rec "$r" 'd["summary"]["reproduced"]')" = 0 ] && [ "$(rec "$r" 'd["fingerprint"]')" != "$(fp)" ]; then
        say "round $r was dry but attacked older code: attacking again"
        cmd_attack --round "$r" || return $?
      else
        say "round $r: completed earlier for this spec; reusing its findings"
      fi
    else
      cmd_attack --round "$r" || { rc=$?; say "stopping: attack round $r did not complete (rc=$rc)"; return $rc; }
    fi
    if [ "$(rec "$r" 'd["summary"]["reproduced"]')" = 0 ]; then
      say "round $r: no reproducible findings; stopping early"
      [ "$SDLC_CALIBRATE" = auto ] && { cmd_calibrate || say "calibration did not complete (the report will say so)"; }
      break
    fi
    if gate_fresh_pass && [ -n "$(ls -d "$RUN"/gate/*-build-r"$r"-* 2>/dev/null)" ]; then consolidate "$r" || true; continue; fi
    cmd_build --round "$r" || { rc=$?; say "fix round $r did not pass the gate (rc=$rc)"; return $rc; }
    consolidate "$r" || true  # best effort: the fix passed the gate, the run goes on whatever happens here
  done
}

cmd_report() {
  load_config
  "${TOOL[@]}" report --project "$PROJECT" --run "$RUN" --attacker-cfg "$(attacker_cfg)"
}

cmd_run() {
  load_config; require_approval
  [ -f "$RUN/REPORT.md" ] && mv "$RUN/REPORT.md" "$RUN/REPORT.prev.md"
  if [ ! -f "$RUN/tests.lock" ] || [ "$(j "$RUN/tests.lock" 'd.get("spec_sha","")')" != "$(spec_sha)" ]; then cmd_tests; fi
  require_no_gaps
  if [ "$SDLC_DESIGN" = 1 ] && ! design_valid; then cmd_design; fi
  local rc=0 rrc=0
  cmd_loop || rc=$?
  cmd_report || rrc=$?
  say "final report: .sdlc/REPORT.md"
  [ $rc -ne 0 ] && return $rc
  return $rrc
}

# upgrade [--apply]: bring this project's harness parts (build.gradle.kts by three-way merge, src/sdlcPitest,
# missing gate.config keys) to the current templates; shows the diff, writes only with --apply. Locked files
# change, so a re-lock with the user's words follows (sdlc.sh relock --relayed "...").
cmd_upgrade() {
  local apply=()
  [ "${1:-}" = --apply ] && apply=(--apply)
  "${TOOL[@]}" upgrade --project "$PROJECT" --skill-dir "$SKILL_DIR" "${apply[@]+"${apply[@]}"}"
}

# adopt-rules [NAME.yml ...] [--dry-run] --relayed "words": human only. Copies staged consolidate rules into the project's
# rules directory (GATE_RULES, default sdlc-rules), sets GATE_RULES in gate.config, and adds the files to the lock, as one
# batch. Rules only add. The gate then runs them in its static step; run it again, since the code fingerprint changed.
cmd_adopt_rules() {
  load_config
  local names=() dry=0 out files
  while [ $# -gt 0 ] && [ "$1" != --relayed ]; do
    if [ "$1" = --dry-run ]; then dry=1; else names+=("$1"); fi
    shift
  done
  local args=(--project "$PROJECT" --run "$RUN" --rules-dir "${GATE_RULES:-sdlc-rules}")
  if [ "$dry" = 1 ]; then python3 "$SKILL_DIR/scripts/adopt_rules.py" "${args[@]}" --dry-run ${names[@]+"${names[@]}"}; return; fi
  [ -f "$RUN/tests.lock" ] || die "no tests.lock: adopt rules after the tests are locked"
  human_ok "adopt the staged rules into the locked build" "$@"
  out="$(python3 "$SKILL_DIR/scripts/adopt_rules.py" "${args[@]}" --how "$HOW" --note "${NOTE:-}" ${names[@]+"${names[@]}"})" \
    || { printf '%s\n' "$out"; die "adoption refused: nothing was changed"; }
  printf '%s\n' "$out"
  files="$(python3 -c 'import json,sys; print(" ".join(json.loads(sys.argv[1])["files"]))' "$out")"
  if [ -n "$files" ]; then
    # shellcheck disable=SC2086
    "${TOOL[@]}" lock "$PROJECT" "$RUN/tests.lock" --add $files --reason "adopt rules ($HOW): ${NOTE:-}" > /dev/null
    echo "$(date +%FT%T) adopted rules ($HOW): ${NOTE:-} [$files]" >> "$RUN/violations.log"
    say "adopted: $files (locked). Run the gate again: the code fingerprint changed."
  else
    say "nothing new to adopt"
  fi
}

cmd_relock() {
  load_config
  human_ok "re-lock the current tests and build files" "$@"
  echo "$(date +%FT%T) relock ($HOW): ${NOTE:-}" >> "$RUN/violations.log"
  "${TOOL[@]}" lock "$PROJECT" "$RUN/tests.lock" --spec-sha "$(spec_sha)"
}

cmd_resolve_disputes() {
  [ -s "$RUN/disputes.md" ] || die "no open disputes"
  cat "$RUN/disputes.md"
  human_ok "mark these disputes as ruled on" "$@"
  mkdir -p "$RUN/disputes.resolved"; local f; f="$RUN/disputes.resolved/$(date +%Y%m%d-%H%M%S).md"
  { cat "$RUN/disputes.md"; printf '\nRuling (%s): %s\n' "$HOW" "${NOTE:-}"; } > "$f"; : > "$RUN/disputes.md"
  say "disputes archived in ${f#"$PROJECT"/}"
}

cmd_status() {
  for f in requirements.md spec.md spec.approved tests.lock spec-gaps.md spec-gaps.accepted design/design.done escalations.md REPORT.md; do
    printf '%-22s %s\n' "$f" "$([ -e "$RUN/$f" ] && echo yes || echo -)"
  done
  ls -d "$RUN"/attack/round-* 2>/dev/null | sed 's|.*/||' | tr '\n' ' '; echo
  ls -td "$RUN"/gate/* 2>/dev/null | head -1 | sed 's|^|last gate: |'
}

# notify CODE: tell the human that a long command stopped, so a stop for a decision does not sit unseen.
# SDLC_NOTIFY=auto (default: a macOS notification) | off. SDLC_NOTIFY_CMD, if set, is run instead with
# the exit code and the message as $1 and $2.
notify() {
  local code="$1" msg
  [ "${SDLC_NOTIFY:-auto}" = off ] && return 0
  case "$code" in
    0) msg="finished" ;; 3) msg="dispute: a human must rule (exit 3)" ;; 4) msg="gate failing or stopped (exit 4)" ;;
    5) msg="attack round incomplete (exit 5)" ;; 6) msg="tests rejected (exit 6)" ;;
    7) msg="waiting for a human decision (exit 7)" ;; *) msg="stopped (exit $code)" ;;
  esac
  msg="sdlc $CMD in $(basename "$PROJECT"): $msg"
  if [ -n "${SDLC_NOTIFY_CMD:-}" ]; then
    bash -c "$SDLC_NOTIFY_CMD" sdlc-notify "$code" "$msg" > /dev/null 2>&1 || true
  elif command -v osascript > /dev/null 2>&1; then
    osascript -e 'on run argv' -e 'display notification (item 1 of argv) with title "adversarial-sdlc"' -e 'end run' \
      "$msg" > /dev/null 2>&1 || true
  fi
}
case "$CMD" in
  spec|tests|build|attack|calibrate|loop|run|repair-tests|design) trap 'notify $?' EXIT ;;
esac

case "$CMD" in
  init|status|watch|help|-h|--help) ;;
  *) take_mutex ;;
esac
case "$CMD" in
  init) cmd_init "$@" ;;
  spec) cmd_spec "$@" ;;
  approve) cmd_approve "$@" ;;
  tests) cmd_tests ;;
  accept-gaps) cmd_accept_gaps "$@" ;;
  repair-tests) cmd_repair_tests "$@" ;;
  accept-repair) cmd_accept_repair "$@" ;;
  design) cmd_design ;;
  build) cmd_build "$@" ;;
  attack) cmd_attack "$@" ;;
  calibrate) cmd_calibrate "$@" ;;
  consolidate) cmd_consolidate "$@" ;;
  adopt-rules) cmd_adopt_rules "$@" ;;
  loop) cmd_loop ;;
  escalate) load_config; "${TOOL[@]}" escalate --project "$PROJECT" --run "$RUN" ;;
  report) cmd_report ;;
  run) cmd_run ;;
  gate) load_config; "$SKILL_DIR/scripts/gate.sh" --project "$PROJECT" "$@" ;;
  relock) cmd_relock "$@" ;;
  upgrade) cmd_upgrade "$@" ;;
  revalidate) cmd_revalidate "$@" ;;
  watch) exec python3 "$SKILL_DIR/scripts/watch.py" "$PROJECT" "$@" ;;
  resolve-disputes) cmd_resolve_disputes "$@" ;;
  status) cmd_status ;;
  *) sed -n '2,24p' "$0" ;;
esac
