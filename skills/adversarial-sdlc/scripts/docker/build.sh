#!/usr/bin/env bash
# Build the sdlc Docker images and record their IDs.
#   build.sh [--platform linux/amd64,linux/arm64] [--no-warmup]
# The default path uses plain `docker build` for the native platform (Docker Desktop, Colima, CI).
# --platform LIST additionally runs `docker buildx build --platform LIST` (no --load) per image, only to
# prove that those platforms build.
# Writes SDLC_DOCKER_ID_{GATE,ROLE,EGRESS}=sha256:... to ${SDLC_DOCKER_STATE:-~/.cache/sdlc-docker/<docker context>}/images.env (atomically).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL="$(cd "$HERE/../.." && pwd)"
# Image IDs are per daemon: state is kept per Docker context (same rule as run.py).
CTX="${DOCKER_CONTEXT:-$(docker context show 2>/dev/null || true)}"; CTX="$(printf '%s' "${CTX:-default}" | tr -c 'A-Za-z0-9_.-' _)"
STATE="${SDLC_DOCKER_STATE:-${XDG_CACHE_HOME:-$HOME/.cache}/sdlc-docker/$CTX}"
CONTEXT="${SDLC_DOCKER_CONTEXT:-$HERE}"   # override is for tests only

platforms="" warmup=1
while [ $# -gt 0 ]; do
  case "$1" in
    --platform) [ $# -ge 2 ] || { echo "build.sh: --platform needs a value" >&2; exit 2; }; platforms="$2"; shift 2 ;;
    --platform=*) platforms="${1#--platform=}"; shift ;;
    --no-warmup) warmup=0; shift ;;
    -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
    *) echo "build.sh: unknown argument: $1" >&2; exit 2 ;;
  esac
done

command -v docker >/dev/null 2>&1 || { echo "build.sh: docker not found" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "build.sh: the Docker daemon is not reachable" >&2; exit 1; }
for f in gate.Dockerfile role.Dockerfile egress.Dockerfile; do
  [ -f "$HERE/$f" ] || { echo "build.sh: missing $HERE/$f" >&2; exit 1; }
done
[ -f "$CONTEXT/egress.py" ] || { echo "build.sh: missing $CONTEXT/egress.py" >&2; exit 1; }

start=$SECONDS
img_id() { docker image inspect --format '{{.Id}}' "$1"; }

echo "== sdlc-gate" >&2
docker build -f "$HERE/gate.Dockerfile" -t sdlc-gate:local "$CONTEXT" >&2
ID_GATE="$(img_id sdlc-gate:local)"

# BuildKit cannot resolve a bare image ID in FROM, so the base is the tag just built, and build.sh
# verifies that the tag still points at the recorded gate ID afterwards.
echo "== sdlc-role (BASE=sdlc-gate:local = $ID_GATE)" >&2
docker build -f "$HERE/role.Dockerfile" --build-arg "BASE=sdlc-gate:local" -t sdlc-role:local "$CONTEXT" >&2
[ "$(img_id sdlc-gate:local)" = "$ID_GATE" ] || { echo "build.sh: sdlc-gate:local changed during the build" >&2; exit 1; }
ID_ROLE="$(img_id sdlc-role:local)"

echo "== sdlc-egress" >&2
docker build -f "$HERE/egress.Dockerfile" -t sdlc-egress:local "$CONTEXT" >&2
ID_EGRESS="$(img_id sdlc-egress:local)"

if [ -n "$platforms" ]; then
  # Cross-platform proof builds. Nothing is loaded into the image store. The role image is built on the
  # multi-platform gate image through an OCI-layout named context, so every platform gets its own base.
  oci="$(mktemp -d "${TMPDIR:-/tmp}/sdlc-oci.XXXXXX")"
  trap 'rm -rf "$oci"' EXIT
  echo "== buildx gate for $platforms" >&2
  docker buildx build --platform "$platforms" -f "$HERE/gate.Dockerfile" \
    --output "type=oci,dest=$oci/gate.tar" "$CONTEXT" >&2
  mkdir "$oci/gate" && tar -xf "$oci/gate.tar" -C "$oci/gate"
  echo "== buildx role for $platforms" >&2
  docker buildx build --platform "$platforms" -f "$HERE/role.Dockerfile" \
    --build-context "sdlc-gate-multi=oci-layout://$oci/gate" --build-arg BASE=sdlc-gate-multi "$CONTEXT" >&2
  echo "== buildx egress for $platforms" >&2
  docker buildx build --platform "$platforms" -f "$HERE/egress.Dockerfile" "$CONTEXT" >&2
  rm -rf "$oci"; trap - EXIT
fi

mkdir -p "$STATE"
tmp="$(mktemp "$STATE/images.env.XXXXXX")"
trap 'rm -f "$tmp"' EXIT
{
  echo "SDLC_DOCKER_ID_GATE=$ID_GATE"
  echo "SDLC_DOCKER_ID_ROLE=$ID_ROLE"
  echo "SDLC_DOCKER_ID_EGRESS=$ID_EGRESS"
} > "$tmp"
chmod 0644 "$tmp"
mv -f "$tmp" "$STATE/images.env"
trap - EXIT

if [ "$warmup" = 1 ]; then
  echo "== warmup (Gradle read-only cache volume)" >&2
  "$HERE/warmup.sh" "$SKILL/templates/kotlin" >&2
fi

echo "gate   $ID_GATE"
echo "role   $ID_ROLE"
echo "egress $ID_EGRESS"
echo "state  $STATE/images.env"
echo "total  $((SECONDS - start))s"
