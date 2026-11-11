#!/usr/bin/env bash
# Mechanics of the k8s backend on a local kind cluster. Run by selftest.sh when
# SDLC_TEST_K8S_CONTEXT names a kind context with the sdlc-runner namespace, its ValidatingAdmissionPolicy
# (sdlc-runner-pods) and a node labelled category=sdlc applied. The manifests are not part of this repo.
# kind does not enforce NetworkPolicy, so the network barrier is skipped here (SDLC_K8S_ALLOW_UNVERIFIED=1);
# network isolation is tested on the real cluster with `scripts/k8s/run.py --verify`.
set -uo pipefail
CTX="${SDLC_TEST_K8S_CONTEXT:?set SDLC_TEST_K8S_CONTEXT to a kind context}"
case "$CTX" in kind-*) ;; *) echo "k8s_kind_test: refusing a non-kind context: $CTX" >&2; exit 2 ;; esac
HERE="$(cd "$(dirname "$0")" && pwd)"
S="$HERE/../scripts/sandbox.sh"
K=(kubectl --context "$CTX")
export SDLC_SANDBOX_BACKEND_CODE=k8s SDLC_K8S_CONTEXT="$CTX" SDLC_K8S_ALLOW_TAG=1 SDLC_K8S_ALLOW_UNVERIFIED=1 \
  SDLC_K8S_PULL_SECRET="" SDLC_K8S_CPU=1 SDLC_K8S_MEMORY=2Gi SDLC_K8S_INPUT_MEMORY=1Gi SDLC_K8S_DISK=1Gi
SDLC_K8S_SERVER="$("${K[@]}" config view --minify --context "$CTX" -o jsonpath='{.clusters[0].cluster.server}')"
export SDLC_K8S_SERVER
unset SDLC_K8S_NODE_POOL SDLC_K8S_ALLOW_ANY_NODE
KIND_REC="${SDLC_K8S_STATE:-${XDG_CACHE_HOME:-$HOME/.cache}/sdlc-k8s}/images-kind.env"
SDLC_K8S_IMAGE="$(sed -n 's/^SDLC_K8S_IMAGE=//p' "$KIND_REC" 2>/dev/null)"
[ -n "$SDLC_K8S_IMAGE" ] || { echo "k8s_kind_test: no kind image recorded ($KIND_REC): run scripts/k8s/build.sh --kind <cluster>" >&2; exit 2; }
export SDLC_K8S_IMAGE
fails=0
ok() { echo "  ok   $1"; }
bad() { echo "  FAIL $1"; fails=$((fails + 1)); }
T="$(mktemp -d "${XDG_CACHE_HOME:-$HOME/.cache}/sdlc-k8s-kind-XXXX")"
trap 'rm -rf "$T"' EXIT

mkdir -p "$T/ro/build" "$T/ro/secret"
echo input > "$T/ro/in.txt"; echo k > "$T/ro/secret/key"; echo stale > "$T/ro/build/stale.txt"
out="$("$S" --ro "$T/ro" --rw "$T/ro/build" --deny "$T/ro/secret" --net local -- bash -c \
  "cat $T/ro/in.txt; test -e $T/ro/secret/key && echo SECRET-VISIBLE; rm $T/ro/build/stale.txt; echo made > $T/ro/build/out.txt; exit 7" 2>&1)"
rc=$?
[ "$rc" = 7 ] && ok "the command's exit code comes back" || bad "exit code $rc, want 7: $out"
grep -q '^input$' <<<"$out" && ok "read-only inputs are visible" || bad "input not visible: $out"
grep -q SECRET-VISIBLE <<<"$out" && bad "a --deny subtree was copied in" || ok "--deny subtrees are not copied in"
[ "$(cat "$T/ro/build/out.txt" 2>/dev/null)" = made ] && ok "outputs come back" || bad "no output"
[ ! -e "$T/ro/build/stale.txt" ] && ok "deletions in rw dirs come back" || bad "stale file kept"
[ -n "$(find "$T/ro/build" -name ".sdlc-mount-check-*")" ] && bad "a mount nonce reached the host" || ok "no mount nonce on the host"

out="$("$S" --ro "$T/ro" --rw "$T/ro/build" --net local -- bash -c "echo x >> $T/ro/in.txt && echo WRITE-ALLOWED; exit 0" 2>&1)"
grep -q WRITE-ALLOWED <<<"$out" && bad "a read-only input was writable" || ok "read-only inputs are read-only (kernel)"
[ "$(cat "$T/ro/in.txt")" = input ] && ok "the host input is unchanged" || bad "host input changed"

"$S" --ro "$T/ro" --rw "$T/ro/build" --net local -- ln -s /etc/passwd "$T/ro/build/leak" >/dev/null 2>&1; rc=$?
[ "$rc" = 97 ] && [ ! -L "$T/ro/build/leak" ] && ok "a link in the outputs is refused (97)" || bad "link output: exit $rc"

SDLC_K8S_MAX_LOG_MB=1 "$S" --rw "$T/ro/build" --net local -- bash -c "head -c 3000000 /dev/zero | tr '\0' x" >/dev/null 2>&1; rc=$?
[ "$rc" = 97 ] && ok "output over the log cap fails (97)" || bad "log cap: exit $rc"

mkdir -p "$T/b"
out="$("$S" --rw "$T/b" --net local -- bash -c "head -c 1500000000 /dev/zero > $T/b/big || echo NO-SPACE" 2>&1)"
grep -q NO-SPACE <<<"$out" && ok "writable dirs are bounded (1Gi memory volume)" || bad "volume not bounded: ${out: -200}"
rm -f "$T/b/big"

"$S" --rw "$T/b" --net local -- bash -c "rm -f /tmp/.sdlc-entry-ok; echo x > $T/b/x" >/dev/null 2>&1; rc=$?
[ "$rc" = 97 ] && [ ! -e "$T/b/x" ] && ok "a missing entry marker fails closed, no copy-out" || bad "marker: exit $rc"

rm -f "$T/b/y"
"$S" --rw "$T/b" --net local -- bash -c "echo y > $T/b/y; kill -9 \$PPID" >/dev/null 2>&1; rc=$?
[ "$rc" = 97 ] && [ ! -e "$T/b/y" ] && ok "no confirmed completion status: 97, no copy-out" || bad "lost status: exit $rc"

"$S" --rw "$T/b" --net local -- /nonexistent-command >/dev/null 2>&1; rc=$?
[ "$rc" = 97 ] && ok "a command that cannot start is a sandbox failure (97)" || bad "missing command: exit $rc"

# Node-disk paths Kubernetes adds outside the memory volumes, per-call UIDs, 0600 inputs,
# and ephemeral containers (#6), attached to a live runner pod while its command runs.
mkdir -p "$T/in"; echo private > "$T/in/p.txt"; chmod 600 "$T/in/p.txt"
(
  for _ in $(seq 60); do
    p="$("${K[@]}" -n sdlc-runner get pods -l sdlc=run -o jsonpath='{.items[0].metadata.name}' 2>/dev/null)"
    if [ -n "$p" ] && "${K[@]}" -n sdlc-runner wait --for=condition=Ready "pod/$p" --timeout=60s >/dev/null 2>&1; then
      "${K[@]}" -n sdlc-runner debug "$p" --image=busybox --profile=restricted -- true > "$T/debug.out" 2>&1
      exit 0
    fi
    sleep 1
  done
) &
dbg=$!
out="$(SDLC_K8S_MAX_FILE_MB=8 "$S" --ro "$T/in" --rw "$T/b" --net local -- bash -c '
  sleep 20
  echo "pid1-stdout=$(readlink /proc/1/fd/1)"
  echo LOG-LEAK > /proc/1/fd/1 2>/dev/null; echo LOG-LEAK > /proc/1/fd/2 2>/dev/null
  head -c 20000000 /dev/zero > /dev/termination-log 2>/dev/null || echo TERMLOG-BOUNDED
  echo "uid=$(id -u) owner=$(stat -c %u '"$T/in/p.txt"')"; cat '"$T/in/p.txt"'' 2>&1)"
wait "$dbg"
grep -q '^pid1-stdout=/dev/null$' <<<"$out" && ok "PID 1 does not write the container log" || bad "PID 1 stdout: $out"
grep -q TERMLOG-BOUNDED <<<"$out" && ok "/dev/termination-log is bounded (RLIMIT_FSIZE)" || bad "termination log not bounded"
grep -q '^private$' <<<"$out" && ok "a 0600 input is readable in the pod" || bad "0600 input: $out"
grep -q "denied request: ephemeral containers are not allowed" "$T/debug.out" 2>/dev/null \
  && ok "the admission policy denies ephemeral containers" || bad "ephemeral container: $(head -c 300 "$T/debug.out" 2>/dev/null)"
u1="$(sed -n 's/^uid=\([0-9]*\) owner=[0-9]*$/\1/p' <<<"$out")"; o1="$(sed -n 's/^uid=[0-9]* owner=\([0-9]*\)$/\1/p' <<<"$out")"
[ -n "$u1" ] && [ "$u1" != "$o1" ] && [ "$u1" -ge 1000000 ] && ok "the runner's UID ($u1) is not the loader's ($o1)" || bad "uids: $out"
u2="$("$S" --rw "$T/b" --net local -- id -u 2>/dev/null)"
[ -n "$u2" ] && [ "$u2" != "$u1" ] && ok "each call gets a fresh UID" || bad "same uid twice: $u1 $u2"

SDLC_K8S_SERVER=https://wrong.example:6443 "$S" --rw "$T/b" --net local -- true >/dev/null 2>&1; rc=$?
[ "$rc" = 97 ] && ok "a context aimed at another API server is refused" || bad "server pin: exit $rc"

IMG="$(python3 "$HERE/../scripts/k8s/run.py" --ids)"; IMG="${IMG#SDLC_K8S_IMAGE=}"
[ -n "$IMG" ] || bad "no image reference (run.py --ids)"
pod='{"apiVersion":"v1","kind":"Pod","metadata":{"name":"sdlc-kind-standalone","namespace":"sdlc-runner"},"spec":{"automountServiceAccountToken":false,"nodeSelector":{"category":"sdlc"},"securityContext":{"runAsNonRoot":true,"runAsUser":10001,"seccompProfile":{"type":"RuntimeDefault"}},"containers":[{"name":"c","image":"'"$IMG"'","command":["true"],"securityContext":{"allowPrivilegeEscalation":false,"capabilities":{"drop":["ALL"]}}}]}}'
res="$("${K[@]}" apply -f - <<<"$pod" 2>&1)"
grep -q "sdlc-runner-pods.*denied request: runner pods must be created by a Job" <<<"$res" \
  && ok "the admission policy denies standalone pods" || bad "standalone pod not denied by the policy: ${res:0:300}"
"${K[@]}" -n sdlc-runner delete pod sdlc-kind-standalone --ignore-not-found --wait=false >/dev/null 2>&1

left="$("${K[@]}" -n sdlc-runner get jobs,pods -l sdlc=run -o name 2>/dev/null)"
[ -z "$left" ] && ok "no Jobs or pods are left behind" || bad "left behind: $left"
echo "k8s_kind_test: $fails failure(s)"
[ "$fails" = 0 ]
