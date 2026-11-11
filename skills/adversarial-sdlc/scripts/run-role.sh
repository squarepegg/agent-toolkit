#!/usr/bin/env bash
# run-role.sh: run one role as a separate headless agent process, sandboxed, sharing nothing.
#
# Usage: run-role.sh ROLE WORKSPACE TASK_FILE RUN_DIR PROJECT_DIR
#
# The role runs in WORKSPACE (a disposable copy holding only its inputs). Inside the OS sandbox
# it can write only to WORKSPACE and its own transcript dir. It cannot read PROJECT_DIR (the
# real project and .sdlc/), other workspaces, or any agent transcripts.
#
# Per-role settings come from sdlc.config (exported by the caller):
#   SDLC_MODEL_<role>, SDLC_THINKING_<role>, SDLC_TOOLS_<role>,
#   SDLC_RUNNER_<role>   custom command instead of pi; it gets the prompt and task on stdin
#   SDLC_SANDBOX_<role>  per-role override of SDLC_SANDBOX (e.g. off for a CLI with its own sandbox)
#   SDLC_PI_BIN, SDLC_PI_ISOLATE, SDLC_PI_EXTENSIONS, SDLC_PI_EXTRA_ARGS, SDLC_ROLE_TIMEOUT
set -uo pipefail

ROLE="$1"; WS="$(cd "$2" && pwd -P)"; TASK="$3"; RUN="$4"; PROJECT="$(cd "$5" && pwd -P)"
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TOOL=(python3 "$SKILL_DIR/scripts/sdlc_tool.py")
PROMPT="$SKILL_DIR/roles/$ROLE.md"
[ -f "$PROMPT" ] || { echo "run-role: no role prompt $PROMPT" >&2; exit 2; }

v() { local n="$1_$ROLE"; printf '%s' "${!n:-}"; }
MODEL="$(v SDLC_MODEL)"; THINK="$(v SDLC_THINKING)"; RUNNER="$(v SDLC_RUNNER)"
TOOLS="$(v SDLC_TOOLS)"; TOOLS="${TOOLS:-read,bash,edit,write,grep,find,ls}"
SBX_ROLE="$(v SDLC_SANDBOX)"; export SDLC_SANDBOX="${SBX_ROLE:-${SDLC_SANDBOX:-auto}}"

STAMP="$(date +%Y%m%d-%H%M%S)-$$"
mkdir -p "$RUN/logs" "$RUN/sessions/$ROLE-$STAMP"
SESS="$RUN/sessions/$ROLE-$STAMP"
LOG="$RUN/logs/$ROLE-$STAMP.log"
WS_FP="$("${TOOL[@]}" fingerprint "$WS")"
SBX="$("$SKILL_DIR/scripts/sandbox.sh" --probe)"
NET_ROLES="${SDLC_SANDBOX_NET_ROLES:-all}"
IMAGES=""
if [ "$SBX" = docker ]; then
  # On Docker, 'all' is already gateway-only (egress proxy), and 'local' would cut the role off from
  # the gateway on the host, so model roles always get 'all' (spec amendment A-5).
  NET_ROLES=all
  IMAGES="$(python3 "$SKILL_DIR/scripts/docker/run.py" --ids | tr '\n' ' ')"
fi
t0=$SECONDS

SANDBOXED=(env -u SDLC_PROJECT "$SKILL_DIR/scripts/sandbox.sh" --rw "$WS" --rw "$SESS" --deny "$PROJECT"
           --net "$NET_ROLES" --
           "${TOOL[@]}" run-timeout "${SDLC_ROLE_TIMEOUT:-3600}" --)
cd "$WS" || exit 2
EXT_ARGS=()
if [ -n "$RUNNER" ]; then
  KIND="custom"
  # Output goes through a pipe: the log lives in .sdlc/, which the sandboxed process cannot touch.
  { cat "$PROMPT"; printf '\n\n---\n\n'; cat "$TASK"; } | "${SANDBOXED[@]}" bash -c "$RUNNER" 2>&1 | cat > "$LOG"
  code=${PIPESTATUS[1]}
else
  KIND="pi"
  args=(-p --session-dir "$SESS" --tools "$TOOLS" -ns -np -nc --no-mcp -na --append-system-prompt "$PROMPT")
  if [ "${SDLC_PI_ISOLATE:-1}" = 1 ]; then
    EXT_ARGS+=(-ne)
    for e in ${SDLC_PI_EXTENSIONS:-}; do EXT_ARGS+=(-e "$e"); done
  fi
  args+=("${EXT_ARGS[@]+"${EXT_ARGS[@]}"}")
  [ -n "$MODEL" ] && args+=(--model "$MODEL")
  [ -n "$THINK" ] && args+=(--thinking "$THINK")
  # shellcheck disable=SC2206
  [ -n "${SDLC_PI_EXTRA_ARGS:-}" ] && args+=(${SDLC_PI_EXTRA_ARGS})
  "${SANDBOXED[@]}" "${SDLC_PI_BIN:-pi}" "${args[@]}" < "$TASK" 2>&1 | cat > "$LOG"
  code=${PIPESTATUS[0]}
fi

L_ROLE="$ROLE" L_KIND="$KIND" L_MODEL="$MODEL" L_THINK="$THINK" L_TOOLS="$TOOLS" L_RUNNER="$RUNNER" \
L_EXT="${EXT_ARGS[*]-}" L_EXTRA="${SDLC_PI_EXTRA_ARGS:-}" L_SBX="$SBX" L_SBXSET="$SDLC_SANDBOX" L_PROMPT="$PROMPT" \
L_IMAGES="$IMAGES" L_NET="$NET_ROLES" L_ROUND="${SDLC_ROUND:-}" \
L_TASK="$TASK" L_WSFP="$WS_FP" L_HARNESS="$("${TOOL[@]}" harness-sha)" L_EXIT="$code" L_SECS="$((SECONDS - t0))" L_LOG="$LOG" L_SESS="$SESS" \
python3 - "$RUN/roles.jsonl" <<'EOF'
import hashlib, json, os, sys, time
e = os.environ
def sha(p):
    try:
        return hashlib.sha256(open(p, "rb").read()).hexdigest()
    except OSError:
        return None
rec = {"role": e["L_ROLE"], "round": int(e["L_ROUND"]) if e["L_ROUND"].isdigit() else None, "runner": e["L_KIND"], "model": e["L_MODEL"] or None, "thinking": e["L_THINK"] or None,
       "tools": e["L_TOOLS"], "custom_runner": e["L_RUNNER"] or None, "extensions": e["L_EXT"],
       "extra_args": e["L_EXTRA"], "sandbox": e["L_SBX"], "sandbox_setting": e["L_SBXSET"],
       "sandbox_net": e["L_NET"],
       "sandbox_images": dict(x.split("=", 1) for x in e["L_IMAGES"].split()) or None,
       "prompt_sha": sha(e["L_PROMPT"]), "task_sha": sha(e["L_TASK"]), "workspace_fp": e["L_WSFP"],
       "harness_sha": e["L_HARNESS"],
       "exit": int(e["L_EXIT"]), "seconds": int(e["L_SECS"]), "log": e["L_LOG"], "session_dir": e["L_SESS"],
       "at": time.strftime("%Y-%m-%dT%H:%M:%S%z")}
with open(sys.argv[1], "a") as f:
    f.write(json.dumps(rec) + "\n")
EOF
echo "role $ROLE exited $code in $((SECONDS - t0))s (model: ${MODEL:-pi default}; sandbox: $SBX; log: $LOG)"
exit $code
