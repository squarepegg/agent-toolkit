#!/usr/bin/env bash
# Long local seed sweep for simulation suites built on the template engine (SIM_SEED_START / SIM_SEEDS).
#
#   seed-sweep.sh [--start N] [--total N] [--chunk N] [--results DIR]... -- <test command>
#
# Runs <test command> once per chunk with SIM_SEED_START and SIM_SEEDS set, so each run stays under the suite's
# @Timeout and the 10,000-seed cap. Stops at the first failing chunk, prints the first failing seed found in the
# JUnit XML reports and the one-seed repro command. Exit: 0 all passed, 1 a seed failed, 2 usage/other error.
#
# Example (Gradle; --rerun because Gradle may consider the test task up to date):
#   seed-sweep.sh --total 10000 --chunk 1000 -- \
#     ./gradlew :my-module:test --tests 'com.example.sim.*' --rerun
#
# Engines without SIM_SEED_START (e.g. the reference engine's original testFixtures engine) ignore the start offset and rerun
# seeds 1..chunk every time. For those, use one run: SIM_SEEDS=<n up to 10000> <test command>, and raise the
# suite's @Timeout if needed.
set -euo pipefail

start=1
total=10000
chunk=1000
results=()

usage() { sed -n '2,16p' "$0" >&2; exit 2; }

while [[ $# -gt 0 ]]; do
  case "$1" in
    --start) start="$2"; shift 2 ;;
    --total) total="$2"; shift 2 ;;
    --chunk) chunk="$2"; shift 2 ;;
    --results) results+=("$2"); shift 2 ;;
    --) shift; break ;;
    -h|--help) usage ;;
    *) echo "unknown option: $1" >&2; usage ;;
  esac
done
[[ $# -gt 0 ]] || usage
[[ "$chunk" =~ ^[0-9]+$ && "$chunk" -ge 1 && "$chunk" -le 10000 ]] || { echo "--chunk must be 1..10000" >&2; exit 2; }
[[ ${#results[@]} -gt 0 ]] || results=(.)

first_failing_seed() {
  # Newest XML reports only (this chunk), first "seed=<n>" in a failure message.
  find "${results[@]}" -path '*test-results*' -name 'TEST-*.xml' -newer "$marker" -print0 2>/dev/null \
    | xargs -0 grep -h -o -E '(on seed=|seed=)-?[0-9]+' 2>/dev/null \
    | head -1 | grep -o -E -- '-?[0-9]+$' || true
}

end=$((start + total - 1))
s=$start
began=$(date +%s)
marker=$(mktemp)
trap 'rm -f "$marker"' EXIT
while [[ $s -le $end ]]; do
  n=$chunk
  (( s + n - 1 > end )) && n=$((end - s + 1))
  echo "[seed-sweep] seeds $s..$((s + n - 1)) ($(( $(date +%s) - began ))s elapsed)" >&2
  touch "$marker"
  if ! SIM_SEED_START="$s" SIM_SEEDS="$n" "$@" >"$marker.log" 2>&1; then
    seed=$(first_failing_seed)
    echo "[seed-sweep] FAILED in chunk $s..$((s + n - 1)). Log: $marker.log" >&2
    if [[ -n "$seed" ]]; then
      echo "[seed-sweep] first failing seed: $seed" >&2
      echo "[seed-sweep] repro: SIM_SEED=$seed $*" >&2
      echo "$seed"
    else
      echo "[seed-sweep] no seed in reports (build/compile failure, non-determinism or timeout?). Read the log." >&2
    fi
    trap - EXIT
    exit 1
  fi
  s=$((s + n))
done
echo "[seed-sweep] all $total seeds ($start..$end) passed in $(( $(date +%s) - began ))s" >&2
