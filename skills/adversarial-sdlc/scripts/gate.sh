#!/usr/bin/env bash
# gate.sh: the single source of truth for "does this pass".
#
# Usage: gate.sh [--project DIR] [--label NAME] [--scratch [--fast]] [--mutation-history DIR [--fresh]]
#
# Reads DIR/gate.config (bash, sourced, exported). By default the gate copies the project to a
# disposable snapshot, then runs every configured step there inside the OS sandbox: it can write
# only to the snapshot and cannot read the project. It writes .sdlc/gate/<ts>-<label>-<pid>/
# containing gate-report.{md,json}, steps/ and logs/.
#
# Fails closed:
# - every configured step must produce a result;
# - integrity, typecheck, test, mutation and static are mandatory and must be "required";
# - the inputs must not change during the run;
# - exit 0 only if all of this holds.
#
# --scratch runs in place (no snapshot) and writes to .sdlc/build/gate-scratch/. It is for the
# builder's own checks inside its sandboxed workspace, and is never evidence. --fast (scratch only)
# skips the mutation and fuzz steps, for quick feedback; that report always fails.
#
# --mutation-history DIR: incremental Pitest. DIR/history.bin from an earlier gate is handed to Pitest
# as build/sdlc-pitest-history/in.bin, and the new out.bin is kept in DIR (needs sdlc.pitestHistory=true
# in the build). The report marks the run incremental, and an incremental run is never final evidence:
# the orchestrator confirms a pass with --fresh (history written, not read).
#
# A step killed at its time limit (GATE_<STEP>_TIMEOUT, default GATE_STEP_TIMEOUT) is reported as timed out;
# its partial reports are not read.
set -uo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL=(python3 "$SKILL_DIR/scripts/sdlc_tool.py")
SANDBOX="$SKILL_DIR/scripts/sandbox.sh"
PROJECT="$PWD"; LABEL="manual"; SCRATCH=0; FAST=0; HIST=""; FRESH=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="$2"; shift 2 ;;
    --label) LABEL="$2"; shift 2 ;;
    --scratch) SCRATCH=1; shift ;;
    --fast) FAST=1; shift ;;
    --mutation-history) HIST="$2"; shift 2 ;;
    --fresh) FRESH=1; shift ;;
    -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
    *) echo "gate.sh: unknown argument $1" >&2; exit 2 ;;
  esac
done
[ "$FAST" = 1 ] && [ "$SCRATCH" != 1 ] && { echo "gate.sh: --fast is for --scratch runs only" >&2; exit 2; }
[ -n "$HIST" ] && [ "$SCRATCH" = 1 ] && { echo "gate.sh: --mutation-history is for snapshot runs only" >&2; exit 2; }
PROJECT="$(cd "$PROJECT" && pwd -P)"
CONFIG="$PROJECT/gate.config"
[ -f "$CONFIG" ] || { echo "gate.sh: no gate.config in $PROJECT" >&2; exit 2; }

GATE_STEPS="integrity typecheck test mutation static fuzz"
GATE_STEP_TIMEOUT=1800
GATE_CONFIG_ERROR=""
set -a
# shellcheck disable=SC1090
. "$CONFIG"
set +a
export SDLC_PROJECT="$PROJECT"

if [ "$SCRATCH" = 1 ]; then
  OUT="$PROJECT/.sdlc/build/gate-scratch"; rm -rf "$OUT"; ROOT="$PROJECT"
else
  OUT="$PROJECT/.sdlc/gate/$(date +%Y%m%d-%H%M%S)-$LABEL-$$"
  ROOT="$(mktemp -d "${TMPDIR:-/tmp}/sdlc-gate-XXXXXX")"
  trap 'rm -rf "$ROOT" "$ROOT".*' EXIT
  "${TOOL[@]}" copy "$PROJECT" "$ROOT" > /dev/null || { echo "gate.sh: snapshot failed" >&2; exit 2; }
fi
mkdir -p "$OUT/logs" "$OUT/steps"
# Written first: an attempt that never writes its report still counts (as a failure).
[ "$SCRATCH" = 1 ] || python3 -c 'import json,sys,time; json.dump({"t": time.time(), "label": sys.argv[2]}, open(sys.argv[1], "w"))' "$OUT/started.json" "$LABEL"
LOCK="$PROJECT/.sdlc/tests.lock"
FP_BEFORE="$("${TOOL[@]}" fingerprint "$ROOT")"
PROJECT_FP=""
[ "$SCRATCH" = 1 ] || PROJECT_FP="$("${TOOL[@]}" fingerprint "$PROJECT")"
# Code under test may write only build output: sources, tests, build files and configs are read-only
# while it runs, and the network is loopback only. Scratch runs (inside the builder's own sandbox)
# use the builder's write scope.
sbx_args() {  # $1: the root the step runs in; sets SBXARGS (the caller's local)
  if [ "$SCRATCH" = 1 ]; then SBXARGS=(--rw "$1" --net local)
  else
    SBXARGS=(--ro "$1" --net local --deny "$PROJECT")
    for g in ${GATE_GENERATED_PATHS:-build .gradle .kotlin}; do mkdir -p "$1/$g"; SBXARGS+=(--rw "$1/$g"); done
  fi
}
run_cmd() {  # root log timeout cmd: run cmd in the sandbox, append its output to log, return its exit code
  local SBXARGS=()
  sbx_args "$1"
  # Pipe, not a file redirect: sandboxed processes must not hold fds on paths they cannot open.
  (cd "$1" && "$SANDBOX" "${SBXARGS[@]}" -- "${TOOL[@]}" run-timeout "$3" -- bash -c "$4") 2>&1 | cat >> "$2"
  return "${PIPESTATUS[0]}"
}
keep_paths() {  # step root: the outputs the evaluator reads (k8s copies back only these; GATE_<STEP>_OUTPUTS overrides)
  local step="$1" root="$2" v rel out=""
  v="GATE_$(upper "$step")_OUTPUTS"
  if [ -n "${!v:-}" ]; then rel="${!v}"
  else
    case "$step" in
      typecheck) rel="${GATE_CLASS_DIRS:-build/classes/kotlin/main build/classes/java/main}" ;;
      test|fuzz) rel="${GATE_JUNIT_DIR:-build/test-results/test}" ;;
      mutation) rel="${GATE_MUTATION_REPORT:-build/reports/pitest/mutations.xml} build/sdlc-pitest-history" ;;
      static) rel="${GATE_STATIC_REPORT:-build/reports/detekt/detekt.xml}" ;;
      *) rel="" ;;
    esac
  fi
  for x in $rel; do out="$out $root/$x"; done
  printf '%s' "${out# }"
}
clone_root() {  # copy-on-write where the filesystem has it (APFS clonefile, reflink), else a plain copy
  cp -cR "$1" "$2" 2>/dev/null || cp -a --reflink=auto "$1" "$2" 2>/dev/null || cp -R "$1" "$2"
}

# Speed spec amendment A. On the k8s backend the steps after integrity run at the same time, each in its own
# clone of the snapshot (A1), and mutation is sharded (A2) with Pitest threads from the sandbox's CPUs (A3).
# The docker backend shares this machine and scratch runs share the builder's tree: both keep the sequence.
# Either way the report is the sequence's: its skip rules are applied to the results in step order.
BACKEND="$("$SANDBOX" --probe --net local 2>/dev/null || echo none)"
PAR=1; SHARDS_MAX=1; THREADS_ARG=""
if [ "$SCRATCH" != 1 ] && [ "$BACKEND" = k8s ]; then
  PAR="${SDLC_GATE_PARALLEL:-0}"   # 0: no bound
  # Step pods: the best measured size (a Kotlin parser project, 2026-11-05: 6 CPUs, 12 GiB; with the runner's 4 CPUs and 8 GiB the
  # gate took 359-382 s instead of 310-320 s). Callers may still set their own.
  export SDLC_K8S_CPU="${SDLC_K8S_CPU:-6}" SDLC_K8S_MEMORY="${SDLC_K8S_MEMORY:-12Gi}"
  # Shard, thread and unit-size arguments are build-tool arguments: they are added only to a Gradle mutation
  # command (the Kotlin template's contract). Any other command runs exactly as configured, unsharded.
  MCMD="${GATE_MUTATION_CMD:-}"; MCMD="${MCMD#"${MCMD%%[![:space:]]*}"}"; MCMD="${MCMD%% *}"
  case "$MCMD" in ./gradlew|gradlew|gradle|*/gradlew|*/gradle) GRADLE_MUTATION=1 ;; *) GRADLE_MUTATION=0 ;; esac
  if [ -n "${GATE_MUTATION_SHARD_ARGS:-}" ] && [ "$GRADLE_MUTATION" = 1 ]; then
    SHARDS_MAX="${SDLC_MUTATION_SHARDS:-auto}"; [ "$SHARDS_MAX" = auto ] && SHARDS_MAX="${SDLC_K8S_MAX_SHARDS:-8}"
  fi
  if [ -n "${GATE_MUTATION_THREADS_ARG:-}" ] && [ "$GRADLE_MUTATION" = 1 ]; then
    # Pitest threads for unsized runs: CPUs - 1 up to 6 CPUs (measured stable), else 2 CPUs per thread
    # (sdlc_tool CPU_PER_THREAD: oversubscribed minions produce spurious kills).
    c="${SDLC_K8S_CPU:-4}"; c="${c%%.*}"
    if [ "$c" -le 2 ] 2>/dev/null; then th=1; elif [ "$c" -le 6 ]; then th=$((c - 1)); else th=$(((c - 1) / 2)); fi
    THREADS_ARG="${GATE_MUTATION_THREADS_ARG//\{threads\}/$th}"
  fi
elif [ "$SCRATCH" != 1 ]; then
  PAR="${SDLC_GATE_PARALLEL:-1}"
fi
# Placeholders that warmed the nodes (sdlc.sh, during the role before this gate) hold quota: remove them first.
[ "$BACKEND" = k8s ] && [ "$SCRATCH" != 1 ] && python3 "$SKILL_DIR/scripts/k8s/run.py" --unwarm > /dev/null 2>&1
SHARD_PLAN=""
if [ "$SHARDS_MAX" -gt 1 ] 2>/dev/null; then
  # Balanced with the newest earlier gate's kept report. It was written by code under test: it can only make the
  # split worse or make the merge refuse it (the step fails); it cannot make a mutant disappear.
  PREV=""  # gate dirs start with a timestamp: the last match is the newest
  for f in "$PROJECT"/.sdlc/gate/*/evidence/mutations.xml.gz; do [ -f "$f" ] && PREV="$f"; done
  TARGETS="$(sed -n "s/^${GATE_PITEST_TARGETS_PROPERTY:-sdlc.pitestTargets}=//p" "$ROOT/${GATE_PITEST_PROPERTIES:-gradle.properties}" 2>/dev/null | tail -1)"
  if [ -n "$TARGETS" ]; then
    SHARD_PLAN="$OUT/mutation-shards.json"
    # Sized from the previous sharded gate's timings (threads per shard so the shards finish together), within a CPU
    # budget below the namespace quota and the NodePool limit (96 vCPU on dev; the other steps' pods need room). Mutation
    # starts first, biggest shard first, so large pods land on empty nodes (a 29-CPU shard started last waited 241 s).
    UNIT="$(sed -n 's/^sdlc.pitestUnitSize=//p' "$ROOT/${GATE_PITEST_PROPERTIES:-gradle.properties}" 2>/dev/null | tail -1)"
    # Budget from the namespace quota: minus the other parallel steps' pods and a margin for the small helper
    # containers of every pod; 72 when the quota cannot be read.
    if [ -z "${SDLC_K8S_SHARD_CPU_BUDGET:-}" ]; then
      CAP="$(python3 "$SKILL_DIR/scripts/k8s/run.py" --capacity 2>/dev/null || echo 0)"
      OTHER=0; for x in $GATE_STEPS; do case "$x" in integrity|mutation) ;; *) v="GATE_$(printf '%s' "$x" | tr '[:lower:]-' '[:upper:]_')_MODE"; [ "${!v:-required}" = off ] || OTHER=$((OTHER + 1)) ;; esac; done
      B=$(( ${CAP:-0} - OTHER * ${SDLC_K8S_CPU%%.*} - 8 ))
      [ "${CAP:-0}" -gt 0 ] 2>/dev/null && [ "$B" -gt 8 ] && SDLC_K8S_SHARD_CPU_BUDGET="$B"
    fi
    "${TOOL[@]}" mutation-shards --previous "$PREV" --max "$SHARDS_MAX" --per-shard "${SDLC_MUTATION_SHARD_MUTANTS:-150}" \
      --template "$GATE_MUTATION_SHARD_ARGS" --targets "$TARGETS" --unit-size "${UNIT:-10}" \
      --max-threads "$(( (${SDLC_K8S_MAX_SHARD_CPU:-29} - 1) / 2 ))" --cpu-budget "${SDLC_K8S_SHARD_CPU_BUDGET:-72}" \
      > "$SHARD_PLAN" || SHARD_PLAN=""
    [ -n "$SHARD_PLAN" ] && [ "$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$SHARD_PLAN")" -gt 1 ] || SHARD_PLAN=""
  fi
fi

run_mutation_shards() {  # root log timeout cmd: every shard in its own clone and sandbox, then the merged report
  local root="$1" log="$2" tmo="$3" cmd="$4" n i rc=0 c pids=() reports=() sargs cpu mem targ logs=() exits=()
  n="$(python3 -c 'import json,sys; print(len(json.load(open(sys.argv[1]))))' "$SHARD_PLAN")"
  echo "mutation: $n shards ($SHARD_PLAN)" >> "$log"
  for ((i = 0; i < n; i++)); do
    clone_root "$root" "$root.shard$i"
    sargs="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[int(sys.argv[2])]["args"])' "$SHARD_PLAN" "$i")"
    cpu="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[int(sys.argv[2])].get("cpu") or "")' "$SHARD_PLAN" "$i")"
    targ="$THREADS_ARG"; mem="${SDLC_K8S_MEMORY:-8Gi}"
    if [ -n "$cpu" ]; then
      th="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[int(sys.argv[2])]["threads"])' "$SHARD_PLAN" "$i")"
      targ="${GATE_MUTATION_THREADS_ARG//\{threads\}/$th}"
      us="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))[int(sys.argv[2])].get("unit_size") or "")' "$SHARD_PLAN" "$i")"
      [ -n "$us" ] && [ -n "${GATE_MUTATION_UNIT_ARG:-}" ] && targ="$targ ${GATE_MUTATION_UNIT_ARG//\{unit\}/$us}"
      # One GiB per minion (the template's -Xmx1g) plus Gradle and Pitest's main process, at least the default.
      m=$(( (3 * th + 1) / 2 + 4 )); d="${mem%Gi}"; [ "$d" -gt "$m" ] 2>/dev/null && m="$d"; mem="${m}Gi"
    else
      cpu="${SDLC_K8S_CPU:-4}"
    fi
    echo "\$ $cmd $sargs $targ   (cpu $cpu, memory $mem)" > "$log.shard$i"
    ( SDLC_K8S_CPU="$cpu" SDLC_K8S_MEMORY="$mem" SDLC_K8S_KEEP="$(keep_paths mutation "$root.shard$i")" \
        run_cmd "$root.shard$i" "$log.shard$i" "$tmo" "$cmd $sargs $targ"
      echo "$?" > "$log.shard$i.exit" ) &
    pids+=("$!")
  done
  wait "${pids[@]}"
  for ((i = 0; i < n; i++)); do logs+=("$log.shard$i"); exits+=("$(cat "$log.shard$i.exit" 2>/dev/null || echo 97)"); done
  # Timings for the next gate's plan (they steer the schedule only).
  "${TOOL[@]}" shard-times --plan "$SHARD_PLAN" --out "${SHARD_PLAN%.json}.result.json" --logs "${logs[@]}" \
    --exits "${exits[@]}" --default-threads "${THREADS_ARG:+$(( ${SDLC_K8S_CPU:-4} - 1 ))}" >> "$log" 2>&1 || true
  for ((i = 0; i < n; i++)); do
    c="${exits[$i]}"
    { echo; echo "===== shard $i (exit $c) ====="; cat "$log.shard$i"; } >> "$log"; rm -f "$log.shard$i" "$log.shard$i.exit"
    # The step's exit code: the first shard failure, but a timeout wins (so it is reported as one).
    if [ "$c" != 0 ]; then
      if [ "$rc" = 0 ] || [ "$c" = 124 ]; then rc="$c"; fi
    fi
    mx="$root.shard$i/${GATE_MUTATION_REPORT:-build/reports/pitest/mutations.xml}"
    if [ -f "$mx" ] && [ ! -L "$mx" ]; then reports+=("$mx"); else reports+=("-"); fi
  done
  local merged="$root/${GATE_MUTATION_REPORT:-build/reports/pitest/mutations.xml}"
  rm -f "$merged"; mkdir -p "$(dirname "$merged")"
  if [ "$rc" = 0 ]; then
    local merge_out
    if ! merge_out="$("${TOOL[@]}" merge-mutations --shards "$SHARD_PLAN" --out "$merged" "${reports[@]}" 2>&1)"; then
      rm -f "$merged"; echo "$merge_out" >> "$log"
      if [[ "$merge_out" == *": no report" ]]; then
        # Every shard succeeded, but one found nothing to mutate (its classes are gone since the previous gate): Pitest
        # writes no report then. The reference is one unsharded run; it costs time, never signal.
        echo "mutation: falling back to one unsharded run" >> "$log"
        run_cmd "$root" "$log" "$tmo" "$cmd $THREADS_ARG"; rc=$?
      else
        rc=1
      fi
    else
      echo "$merge_out" >> "$log"
    fi
  fi
  return "$rc"
}

exec_step() {  # step order root skip-reason: run (or skip) one step and write its result file
  local step="$1" order="$2" root="$3" skip="$4"
  local U mode_var cmd_var tmo_var mode cmd tmo log args started t0 code HD INCR step_file
  U="$(upper "$step")"; mode_var="GATE_${U}_MODE"; cmd_var="GATE_${U}_CMD"
  mode="${!mode_var:-required}"; cmd="${!cmd_var:-}"
  tmo_var="GATE_${U}_TIMEOUT"; tmo="${!tmo_var:-$GATE_STEP_TIMEOUT}"
  log="$OUT/logs/$step.log"
  args=(--dir "$OUT" --root "$root" --name "$step" --mode "$mode" --order "$order" --log "$log" --lock "$LOCK")
  if [ "$mode" = off ]; then :
  elif [ "$step" != integrity ] && [ -z "$cmd" ]; then args+=(--not-run "no $cmd_var configured")
  elif [ "$FAST" = 1 ] && [[ " mutation fuzz " == *" $step "* ]]; then
    args+=(--cmd "$cmd" --not-run "skipped by --fast (quick builder feedback; the orchestrator's gate runs it)")
  elif [ -n "$skip" ]; then
    args+=(--cmd "$cmd" --not-run "$skip")
    [ -f "$log" ] && printf '\n(result discarded: %s; the steps ran in parallel)\n' "$skip" >> "$log"
  else
    started="$(python3 -c 'import time; print(time.time())')"; t0=$SECONDS; code=0
    HD="$root/build/sdlc-pitest-history"; INCR=0
    if [ "$step" = mutation ]; then
      # Only the gate supplies Pitest history: whatever earlier steps (code under test) left here is discarded.
      # Sharded runs use none (each shard covers other classes than the history's run).
      rm -rf "$HD"; mkdir -p "$HD"
      if [ -z "$SHARD_PLAN" ] && [ -n "$HIST" ] && [ "$FRESH" != 1 ] && [ -s "$HIST/history.bin" ]; then
        cp "$HIST/history.bin" "$HD/in.bin" && INCR=1
      fi
    fi
    if [ "$step" = integrity ]; then
      echo "integrity: verify $LOCK against the snapshot; forbid /${GATE_FORBID_REGEX:-}/ in main sources" > "$log"
    elif [ "$step" = mutation ] && [ -n "$SHARD_PLAN" ]; then
      echo "\$ $cmd (sharded)" > "$log"
      run_mutation_shards "$root" "$log" "$tmo" "$cmd"; code=$?
    else
      echo "\$ $cmd" > "$log"
      [ "$step" = mutation ] && [ -n "$THREADS_ARG" ] && cmd="$cmd $THREADS_ARG"
      SDLC_K8S_KEEP="$(keep_paths "$step" "$root")" run_cmd "$root" "$log" "$tmo" "$cmd"; code=$?
      if [ "$step" = mutation ] && [ -n "$HIST" ] && [ "$code" = 0 ] && [ -s "$HD/out.bin" ]; then
        mkdir -p "$HIST" && cp "$HD/out.bin" "$HIST/history.bin.tmp" && mv "$HIST/history.bin.tmp" "$HIST/history.bin"
      fi
    fi
    # run-timeout exits 124 when it kills the step at the deadline.
    if [ "$code" = 124 ] && [ $((SECONDS - t0)) -ge "$tmo" ]; then args+=(--timed-out); fi
    [ "$INCR" = 1 ] && args+=(--incremental)
    args+=(--cmd "${cmd:-builtin}" --exit "$code" --seconds "$((SECONDS - t0))" --started "$started" --timeout "$tmo")
  fi
  step_file="$OUT/steps/$(printf '%02d' "$order")-$step.json"
  if ! "${TOOL[@]}" gate-step "${args[@]}" >> "$OUT/logs/evaluator.log" 2>&1 || [ ! -s "$step_file" ]; then
    # Evaluator crashed: record a failure rather than leaving a hole in the inventory.
    printf '{"name": "%s", "mode": "%s", "status": "fail", "seconds": 0, "metrics": {}, "checks": [{"ok": false, "what": "evaluator crashed; see logs/evaluator.log"}]}\n' \
      "$step" "$mode" > "$step_file"
  fi
}

report_step() {  # step order root: keep evidence, print the status, update FAILED_REQUIRED
  local step="$1" order="$2" root="$3" step_file status mode_var mode
  step_file="$OUT/steps/$(printf '%02d' "$order")-$step.json"
  mode_var="GATE_$(upper "$step")_MODE"; mode="${!mode_var:-required}"
  # Keep the per-mutant result: two gates with different scores must be comparable mutant by mutant. Written by
  # code under test (or merged from its shards), so links are refused (a link could point at any file here).
  MX="$root/${GATE_MUTATION_REPORT:-build/reports/pitest/mutations.xml}"
  if [ "$step" = mutation ] && [ -f "$MX" ] && [ ! -L "$MX" ]; then
    mkdir -p "$OUT/evidence" && gzip -c < "$MX" > "$OUT/evidence/mutations.xml.gz"
  fi
  status="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["status"])' "$step_file" 2>/dev/null || echo fail)"
  echo "gate step $step: $status"
  if [ "$status" != pass ] && [ "$mode" != optional ] && [ "$mode" != off ]; then FAILED_REQUIRED=1; fi
}

FAILED_REQUIRED=0; ORDER=0
upper() { printf '%s' "$1" | tr '[:lower:]-' '[:upper:]_'; }
skip_reason() {  # the sequence's rule: after a required failure, test, mutation and fuzz do not count
  if [ "$FAILED_REQUIRED" = 1 ] && [[ " test mutation fuzz " == *" $1 "* ]]; then echo "an earlier required step failed"; fi
}
if [ "$PAR" = 1 ]; then
  for step in $GATE_STEPS; do
    ORDER=$((ORDER + 1))
    exec_step "$step" "$ORDER" "$ROOT" "$(skip_reason "$step")"
    report_step "$step" "$ORDER" "$ROOT"
  done
else
  # Integrity is built in (no code under test) and runs first, in the snapshot itself.
  pids=(); running=0
  # Mutation first (its shards are the biggest pods), then the rest in gate order; ORDER stays the gate order.
  LAUNCH="$(for x in $GATE_STEPS; do [ "$x" = mutation ] && printf 'mutation '; done; for x in $GATE_STEPS; do [ "$x" = mutation ] || printf '%s ' "$x"; done)"
  for step in $LAUNCH; do
    ORDER=0; for x in $GATE_STEPS; do ORDER=$((ORDER + 1)); [ "$x" = "$step" ] && break; done
    U="$(upper "$step")"; mode_var="GATE_${U}_MODE"; cmd_var="GATE_${U}_CMD"
    # Built-in or not run at all (integrity, off, no command): no code under test, in the snapshot itself.
    if [ "$step" = integrity ] || [ "${!mode_var:-required}" = off ] || [ -z "${!cmd_var:-}" ]; then
      exec_step "$step" "$ORDER" "$ROOT" ""; continue
    fi
    clone_root "$ROOT" "$ROOT.$step"
    exec_step "$step" "$ORDER" "$ROOT.$step" "" &
    pids+=("$!"); running=$((running + 1))
    if [ "$PAR" -gt 1 ] && [ "$running" -ge "$PAR" ]; then wait "${pids[@]}"; pids=(); running=0; fi  # bash 3.2: no wait -n
  done
  [ "${#pids[@]}" -gt 0 ] && wait "${pids[@]}"
  ORDER=0
  for step in $GATE_STEPS; do
    ORDER=$((ORDER + 1)); r="$ROOT"; [ -d "$ROOT.$step" ] && r="$ROOT.$step"
    why="$(skip_reason "$step")"; [ -n "$why" ] && exec_step "$step" "$ORDER" "$r" "$why"
    report_step "$step" "$ORDER" "$r"
  done
fi

# The read-only inputs must be unchanged in the snapshot and in every clone a step or shard ran in.
FP_AFTER="$("${TOOL[@]}" fingerprint "$ROOT")"
for r in "$ROOT".*; do
  [ -d "$r" ] || continue
  f="$("${TOOL[@]}" fingerprint "$r")"; [ "$f" = "$FP_BEFORE" ] || FP_AFTER="$f"
done
[ "$SCRATCH" = 1 ] || PROJECT_FP_AFTER="$("${TOOL[@]}" fingerprint "$PROJECT")"
if [ "$SCRATCH" != 1 ] && [ "$PROJECT_FP_AFTER" != "$PROJECT_FP" ]; then PROJECT_FP="changed-during-run"; fi
SPEC_SHA=""; [ -f "$PROJECT/.sdlc/spec.approved" ] && SPEC_SHA="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["sha256"])' "$PROJECT/.sdlc/spec.approved")"
# shasum on macOS, sha256sum on Linux (gate.sh also runs inside containers).
sha256() { if command -v shasum > /dev/null; then shasum -a 256 "$1"; else sha256sum "$1"; fi | cut -d' ' -f1; }
LOCK_SHA=""; [ -f "$LOCK" ] && LOCK_SHA="$(sha256 "$LOCK")"
SCR=(); [ "$SCRATCH" = 1 ] && SCR=(--scratch)
"${TOOL[@]}" gate-report --dir "$OUT" --steps "$GATE_STEPS" --label "$LABEL" --fp-before "$FP_BEFORE" \
  --fp-after "$FP_AFTER" --project-fp "$PROJECT_FP" --spec-sha "$SPEC_SHA" --lock-sha "$LOCK_SHA" \
  --config-sha "$(sha256 "$CONFIG")" --config-error "$GATE_CONFIG_ERROR" "${SCR[@]+"${SCR[@]}"}"
rc=$?
echo "gate report: $OUT/gate-report.md"
exit $rc
