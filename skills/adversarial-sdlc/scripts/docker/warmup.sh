#!/usr/bin/env bash
# Fill the read-only Gradle dependency cache volume (sdlc-gradle-ro) from a locked skeleton.
#   warmup.sh SKELETON_DIR [CMD]
# TRUSTED: run it only on a skeleton of build files (and wrapper). It must never see role-written code.
# Consumers mount the volume read-only at /opt/gradle-ro and set GRADLE_RO_DEP_CACHE=/opt/gradle-ro/caches.
# Default CMD: ./gradlew --console=plain -q sdlcResolveDependencies
# Env: SDLC_DOCKER_STATE (dir holding images.env), SDLC_DOCKER_VOLUME (default sdlc-gradle-ro),
#      SDLC_DOCKER_MEMORY (default 4g), SDLC_DOCKER_CPUS (default 4).
set -euo pipefail

[ $# -ge 1 ] || { echo "usage: warmup.sh SKELETON_DIR [CMD]" >&2; exit 2; }
SKEL="$(cd "$1" && pwd)" || { echo "warmup.sh: no such directory: $1" >&2; exit 2; }
CMD="${2:-./gradlew --console=plain -q sdlcResolveDependencies}"

# Image IDs are per daemon: state is kept per Docker context (same rule as run.py).
CTX="${DOCKER_CONTEXT:-$(docker context show 2>/dev/null || true)}"; CTX="$(printf '%s' "${CTX:-default}" | tr -c 'A-Za-z0-9_.-' _)"
STATE="${SDLC_DOCKER_STATE:-${XDG_CACHE_HOME:-$HOME/.cache}/sdlc-docker/$CTX}"
VOL="${SDLC_DOCKER_VOLUME:-sdlc-gradle-ro}"
MEM="${SDLC_DOCKER_MEMORY:-4g}"
CPUS="${SDLC_DOCKER_CPUS:-4}"

ID_GATE=""
if [ -f "$STATE/images.env" ]; then
  ID_GATE="$(sed -n 's/^SDLC_DOCKER_ID_GATE=//p' "$STATE/images.env" | tail -n 1)"
fi
if [ -z "$ID_GATE" ] || ! docker image inspect "$ID_GATE" >/dev/null 2>&1; then
  echo "warmup.sh: image not built: run scripts/docker/build.sh" >&2
  exit 1
fi

UIDGID="$(id -u):$(id -g)"
docker volume inspect "$VOL" >/dev/null 2>&1 || docker volume create --label sdlc-infra "$VOL" >/dev/null

# The volume root must be writable by the host user. One short root container with only the volume.
# Docker re-applies the image mountpoint's ownership (root) to an EMPTY volume at every container start,
# so a marker file makes it non-empty before the chown, and the ownership then sticks.
docker run --rm --label sdlc-warmup --network none --read-only --user 0:0 \
  --cap-drop ALL --cap-add CHOWN --cap-add DAC_OVERRIDE --security-opt no-new-privileges \
  --mount "type=volume,src=$VOL,dst=/opt/gradle-ro" "$ID_GATE" \
  bash -ec 'touch /opt/gradle-ro/.sdlc-volume; if [ "$(stat -c %u:%g /opt/gradle-ro)" != "$1" ]; then chown -R "$1" /opt/gradle-ro; fi; chown "$1" /opt/gradle-ro/.sdlc-volume' chown "$UIDGID"

# shellcheck disable=SC2016  # expanded inside the container
INNER='
set -eu
cp -r /src/. /work/
cd /work
# Template placeholders (harmless when the skeleton is already instantiated).
grep -rlI --exclude-dir=.git -e __PACKAGE__ -e __NAME__ . 2>/dev/null \
  | while IFS= read -r f; do sed -i -e "s/__PACKAGE__/sdlc.warmup/g" -e "s/__NAME__/warmup/g" "$f"; done || true
export GRADLE_USER_HOME=/opt/gradle-ro
mkdir -p "$GRADLE_USER_HOME/wrapper/dists"
[ -e "$GRADLE_USER_HOME/wrapper/dists/gradle-8.10-bin" ] || cp -r /opt/gradle-dists/. "$GRADLE_USER_HOME/wrapper/dists/"
if [ ! -f ./gradlew ]; then
  /opt/gradle-8.10/bin/gradle --no-daemon -q wrapper --gradle-version 8.10 --distribution-type bin
fi
chmod +x ./gradlew
# KGP 2.0.x registers kotlinNativeBundleConfiguration on every project; it has no linux-aarch64 prebuilt,
# so the template task (which resolves every resolvable configuration) fails on arm64 Linux. JVM builds
# never use it. Make it unresolvable during warm-up only (the init script is removed again below).
mkdir -p "$GRADLE_USER_HOME/init.d"
trap "rm -f \"$GRADLE_USER_HOME/init.d/sdlc-warmup.gradle\"" EXIT
cat > "$GRADLE_USER_HOME/init.d/sdlc-warmup.gradle" <<GRADLE
allprojects { afterEvaluate { configurations.matching { it.name == "kotlinNativeBundleConfiguration" }.all { canBeResolved = false } } }
GRADLE
export GRADLE_OPTS="${GRADLE_OPTS:-} -Dorg.gradle.daemon=false"
bash -c "$1"
# Read-only cache requirements: no lock files, no gc.properties.
find "$GRADLE_USER_HOME/caches" \( -name "*.lock" -o -name gc.properties \) -type f -delete
# Consumers need only caches/modules-2. Drop the rest (daemon logs, native libs, build-script caches,
# wrapper dists) so the volume stays small and holds nothing but resolved dependencies.
find "$GRADLE_USER_HOME/caches" -mindepth 1 -maxdepth 1 ! -name modules-2 -exec rm -rf {} +
find "$GRADLE_USER_HOME" -mindepth 1 -maxdepth 1 ! -name caches ! -name .sdlc-volume -exec rm -rf {} +
'

log="$(mktemp "${TMPDIR:-/tmp}/sdlc-warmup.XXXXXX")"
trap 'rm -f "$log"' EXIT
if docker run --rm --label sdlc-warmup --user "$UIDGID" --cap-drop ALL \
    --security-opt no-new-privileges --read-only --pids-limit 4096 --memory "$MEM" --cpus "$CPUS" \
    --mount "type=bind,src=$SKEL,dst=/src,readonly" \
    --mount "type=volume,src=$VOL,dst=/opt/gradle-ro" \
    --tmpfs /work:exec,mode=1777 --tmpfs /home/sdlc:exec,mode=1777 --tmpfs /tmp:exec,mode=1777 \
    --workdir /work "$ID_GATE" bash -c "$INNER" warmup "$CMD" >"$log" 2>&1; then
  echo "warmup: volume $VOL filled from $SKEL" >&2
else
  rc=$?
  cat "$log" >&2
  echo "warmup.sh: failed (exit $rc)" >&2
  exit "$rc"
fi
