#!/usr/bin/env bash
# sandbox.sh: run a command in a deny-by-default sandbox: a Docker container, or macOS sandbox-exec.
#
# Usage: sandbox.sh [--rw DIR]... [--ro DIR]... [--deny DIR]... [--net all|local|none] -- COMMAND [ARGS]
#        sandbox.sh --probe        prints: docker | sandbox-exec | inherited | none
#
# Backend: SDLC_SANDBOX_BACKEND=docker | sandbox-exec | auto (default: docker if usable, else
# sandbox-exec; --probe records which). The docker backend is scripts/docker/run.py (spec:
# docs/specs/docker-backend.md). It runs one container per call with only the listed dirs
# mounted. --net local and none mean no network; --net all means the model gateway only,
# through an egress proxy. Inside an sdlc container (a marker file in the read-only image), a
# nested call runs directly, because the container already confines it (FM-11).
# The rest of this header describes the sandbox-exec backend.
#
# Policy:
#   reads   denied everywhere except: system dirs (/System /usr /bin /sbin /Library /private/etc /opt
#           /dev), the user cache dir, toolchains (~/Library/Java ~/.sdkman ~/.local), pi's install,
#           the read-only Gradle module cache, this skill, SDLC_SANDBOX_READ, and the --ro/--rw dirs;
#   writes  only to the --rw dirs and a fresh private state dir;
#   denied  always: --deny dirs, agent transcripts (~/.pi/agent/sessions, ~/.codex, ~/.claude*);
#   network --net local: outbound only to localhost and unix sockets (code under test always uses
#           this); --net all: unrestricted (model roles with remote providers).
# Private state: a fresh mktemp dir created here, outside every writable tree, removed afterwards.
# It holds TMPDIR, java.io.tmpdir, a Gradle home (module cache read-only from ~/.gradle/caches; the
# pinned wrapper distribution cloned) and a pi agent dir (copy of auth.json, minimal settings).
# Nothing persists between invocations, so a workload cannot plant state for a later one.
#
# SDLC_SANDBOX: required (default; exit 97 if unavailable) | auto (warn, run unsandboxed) | off.
# Nested call: runs directly only if probes show the current process already enforces this policy
# (it cannot write outside the --rw dirs, cannot list $HOME, cannot read any --deny dir); else exit 97.
set -uo pipefail

RW=(); RO=(); DENY=(); NET="${SDLC_SANDBOX_NET:-all}"
A=()  # the path arguments as given: the docker backend also mounts them at the caller's spelling
while [ $# -gt 0 ]; do
  case "$1" in
    --probe) PROBE=1; shift ;;
    --rw) mkdir -p "$2"; RW+=("$(cd "$2" && pwd -P)"); A+=("$1" "$2"); shift 2 ;;
    --ro) RO+=("$(cd "$2" && pwd -P)"); A+=("$1" "$2"); shift 2 ;;
    --deny) [ -e "$2" ] && DENY+=("$(cd "$2" && pwd -P)"); A+=("$1" "$2"); shift 2 ;;
    --net) NET="$2"; shift 2 ;;
    --) shift; break ;;
    *) echo "sandbox.sh: unknown argument $1" >&2; exit 2 ;;
  esac
done
case "$NET" in all|local) ;; none) NET=local ;; *) echo "sandbox.sh: --net must be all|local|none" >&2; exit 2 ;; esac

# Inside an sdlc container: a role cannot create the image's marker (read-only rootfs).
if [ -r /etc/sdlc-container ] && [ "$(cat /etc/sdlc-container)" = sdlc-docker-backend ]; then
  if [ "${PROBE:-0}" = 1 ]; then echo inherited; exit 0; fi
  [ $# -gt 0 ] || { echo "sandbox.sh: no command" >&2; exit 2; }
  exec "$@"
fi
DOCKER_RUN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/docker/run.py"
K8S_RUN="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)/k8s/run.py"
# Code under test (--net local) may run on a Kubernetes cluster instead (SDLC_SANDBOX_BACKEND_CODE=k8s,
# scripts/k8s/run.py). Model roles (--net all) always stay on the local backend: they need the local gateway.
# k8s never falls back: if it is unusable, the call fails closed (exit 97).
if [ "${SDLC_SANDBOX:-required}" != off ] && [ "${SDLC_SANDBOX_BACKEND_CODE:-}" = k8s ] && [ "$NET" = local ]; then
  if ! why="$(python3 "$K8S_RUN" --check)"; then
    if [ "${PROBE:-0}" = 1 ]; then echo none; exit 0; fi
    echo "sandbox.sh: SDLC_SANDBOX_BACKEND_CODE=k8s but the k8s backend is unavailable: $why" >&2; exit 97
  fi
  if [ "${PROBE:-0}" = 1 ]; then echo k8s; exit 0; fi
  [ $# -gt 0 ] || { echo "sandbox.sh: no command" >&2; exit 2; }
  SDLC_K8S_CHECKED=1 exec python3 "$K8S_RUN" "${A[@]+"${A[@]}"}" --net local -- "$@"
fi
backend() {  # docker | sandbox-exec | unavailable (docker was requested but is not usable)
  case "${SDLC_SANDBOX_BACKEND:-auto}" in
    sandbox-exec) echo sandbox-exec ;;
    docker) if python3 "$DOCKER_RUN" --check; then echo docker; else echo unavailable; fi ;;
    auto) if python3 "$DOCKER_RUN" --check; then echo docker; else echo sandbox-exec; fi ;;
    *) echo "sandbox.sh: SDLC_SANDBOX_BACKEND must be docker|sandbox-exec|auto" >&2; echo unavailable ;;
  esac
}
if [ "${SDLC_SANDBOX:-required}" != off ]; then
  BACKEND="$(backend)"
  if [ "$BACKEND" = docker ]; then
    if [ "${PROBE:-0}" = 1 ]; then echo docker; exit 0; fi
    [ $# -gt 0 ] || { echo "sandbox.sh: no command" >&2; exit 2; }
    SDLC_DOCKER_CHECKED=1 exec python3 "$DOCKER_RUN" "${A[@]+"${A[@]}"}" --net "$NET" -- "$@"
  fi
  if [ "$BACKEND" = unavailable ]; then  # INV-9: fail closed, no silent fallback
    if [ "${PROBE:-0}" = 1 ]; then echo none; exit 0; fi
    why="$(python3 "$DOCKER_RUN" --check -v 2>&1)"
    if [ "${SDLC_SANDBOX:-required}" = auto ]; then
      echo "sandbox.sh: WARNING: running UNSANDBOXED (docker backend unavailable: $why)" >&2; exec "$@"
    fi
    echo "sandbox.sh: SDLC_SANDBOX_BACKEND=docker but the docker backend is unavailable: $why" >&2; exit 97
  fi
fi

confined() {  # does the current process already enforce at least this policy?
  local parent probe
  parent="$(dirname "${RW[0]:-${TMPDIR:-/tmp}}")"
  # Permission bits come from stat (metadata), which the sandbox does not hide; access(2) it does.
  python3 - "$parent" "$HOME" <<'PY' || return 1          # permissions alone would allow both...
import os, stat, sys
def can(path, bit):
    st = os.stat(path)
    return st.st_uid == os.getuid() and bool(st.st_mode & bit)
sys.exit(0 if can(sys.argv[1], stat.S_IWUSR) and can(sys.argv[2], stat.S_IRUSR) else 1)
PY
  probe="$parent/.sdlc-sandbox-probe-$$"
  if ( : > "$probe" ) 2>/dev/null; then rm -f "$probe"; return 1; fi   # ...a sandbox blocks the write
  ls "$HOME" > /dev/null 2>&1 && return 1                                 # ...and the listing
  for d in "${DENY[@]+"${DENY[@]}"}"; do ls "$d" > /dev/null 2>&1 && return 1; done
  return 0
}
mech() { if [ "${SDLC_SANDBOX:-required}" != off ] && command -v sandbox-exec >/dev/null 2>&1; then echo sandbox-exec; else echo none; fi; }

if [ "${PROBE:-0}" = 1 ]; then if confined; then echo inherited; else mech; fi; exit 0; fi
[ $# -gt 0 ] || { echo "sandbox.sh: no command" >&2; exit 2; }
if confined; then exec "$@"; fi   # an outer sandbox already enforces at least this policy
if [ "${SDLC_IN_SANDBOX:-0}" = 1 ] && [ "$(mech)" = sandbox-exec ]; then
  # Marked as sandboxed, but the probes disagree: trust the probes. If an outer sandbox exists,
  # sandbox-exec below fails and the command does not run.
  :
fi
if [ "$(mech)" = none ]; then
  case "${SDLC_SANDBOX:-required}" in
    off) exec "$@" ;;
    auto) echo "sandbox.sh: WARNING: running UNSANDBOXED (no sandbox-exec)" >&2; exec "$@" ;;
    *) echo "sandbox.sh: SDLC_SANDBOX=required but no sandbox mechanism is available" >&2; exit 97 ;;
  esac
fi

H="$(cd "$HOME" && pwd -P)"
SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
P="$(mktemp -d "${TMPDIR:-/tmp}/sdlc-sbx-XXXXXX")"; P="$(cd "$P" && pwd -P)"
trap 'rm -rf "$P"' EXIT
mkdir -p "$P/tmp" "$P/gradle/wrapper/dists" "$P/pi-agent"
for d in "${RW[@]+"${RW[@]}"}" "${RO[@]+"${RO[@]}"}"; do
  props="$d/gradle/wrapper/gradle-wrapper.properties"
  [ -f "$props" ] || continue
  dist="$(sed -n 's#^distributionUrl=.*/\([^/]*\)\.zip$#\1#p' "$props")"
  if [ -n "$dist" ] && [ -d "$H/.gradle/wrapper/dists/$dist" ]; then
    cp -Rc "$H/.gradle/wrapper/dists/$dist" "$P/gradle/wrapper/dists/" 2>/dev/null \
      || cp -R "$H/.gradle/wrapper/dists/$dist" "$P/gradle/wrapper/dists/"
  fi
  break
done
[ -f "$H/.pi/agent/auth.json" ] && cp "$H/.pi/agent/auth.json" "$P/pi-agent/auth.json"
python3 - "$H/.pi/agent/settings.json" "$P/pi-agent/settings.json" <<'EOF'
import json, sys
try:
    src = json.load(open(sys.argv[1]))
except (OSError, ValueError):
    src = {}
json.dump({k: src[k] for k in ("defaultProvider", "defaultModel", "defaultThinkingLevel") if k in src},
          open(sys.argv[2], "w"))
EOF

q() { printf '"%s"' "$(printf '%s' "$1" | sed 's/\\/\\\\/g; s/"/\\"/g')"; }
UC="$(getconf DARWIN_USER_CACHE_DIR 2>/dev/null || true)"; [ -n "$UC" ] && [ -d "$UC" ] && UC="$(cd "$UC" && pwd -P)"
READ_OK=(/System /usr /bin /sbin /Library /private/etc /private/var/db/timezone /opt /dev
         "$H/.pi/agent/install" "$H/.pi/agent/bin" "$H/.pi/agent/git" "$H/.pi/agent/npm" "$H/Library/Java"
         "$H/.sdkman" "$H/.local" "$H/.gradle/caches/modules-2" "$SKILL_DIR")
[ -n "$UC" ] && READ_OK+=("$UC")
for r in ${SDLC_SANDBOX_READ:-}; do [ -e "$r" ] && READ_OK+=("$(cd "$r" && pwd -P)"); done
PROFILE="$P/profile.sb"
{
  echo '(version 1)'
  echo '(allow default)'
  echo '(deny file-write*)'
  echo '(allow file-write* (literal "/dev/null") (literal "/dev/zero") (literal "/dev/tty") (literal "/dev/dtracehelper") (literal "/dev/random") (literal "/dev/urandom") (regex #"^/dev/fd/"))'
  echo '(deny file-read-data (subpath "/"))'
  printf '(allow file-read-data (literal "/") (literal "/private") (literal "/private/var")'
  for r in "${READ_OK[@]}"; do [ -e "$r" ] && printf ' (subpath %s)' "$(q "$r")"; done
  for r in "${RO[@]+"${RO[@]}"}"; do printf ' (subpath %s)' "$(q "$r")"; done
  echo ')'
  if [ "$NET" = local ]; then
    echo '(deny network-outbound)'
    echo '(allow network-outbound (remote ip "localhost:*") (remote unix-socket))'
  fi
  printf '(deny file-read* file-read-data file-write* (subpath %s) (subpath %s) (subpath %s) (literal %s)' \
    "$(q "$H/.pi/agent/sessions")" "$(q "$H/.codex")" "$(q "$H/.claude")" "$(q "$H/.claude.json")"
  for d in "${DENY[@]+"${DENY[@]}"}"; do printf ' (subpath %s)' "$(q "$d")"; done
  echo ')'
  printf '(allow file-read* file-read-data file-write* (subpath %s)' "$(q "$P")"
  for d in "${RW[@]+"${RW[@]}"}"; do printf ' (subpath %s)' "$(q "$d")"; done
  echo ')'
} > "$PROFILE"

export TMPDIR="$P/tmp" GRADLE_USER_HOME="$P/gradle" GRADLE_RO_DEP_CACHE="$H/.gradle/caches" \
       PI_CODING_AGENT_DIR="$P/pi-agent" \
       JAVA_TOOL_OPTIONS="-Djava.io.tmpdir=$P/tmp -Djava.net.preferIPv4Stack=true ${JAVA_TOOL_OPTIONS:-}" \
       SDLC_IN_SANDBOX=1
sandbox-exec -f "$PROFILE" "$@"
code=$?
[ $code = 71 ] && echo "sandbox.sh: sandbox-exec could not apply the profile (nested in a foreign sandbox?)" >&2
exit $code
