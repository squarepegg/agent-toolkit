#!/usr/bin/env bash
# k8s/build.sh: build the warm runner image for the k8s code backend and pin it.
#
# Usage: k8s/build.sh [--push | --kind CLUSTER]
#
# The image is the local sdlc-gate image (docker/build.sh) plus the read-only Gradle dependency cache that the
# docker backend mounts as the sdlc-gradle-ro volume, baked in at /opt/gradle-ro. Pods then build offline: the
# runner namespace has no network at all.
#   --push          push to $SDLC_K8S_REGISTRY/$SDLC_K8S_IMAGE_NAME (default registry.example.com/
#                   sdlc-gate-warm) and record the image by
#                   digest in ~/.cache/sdlc-k8s/images.env (SDLC_K8S_IMAGE=repo@sha256:...). Needs `docker login`.
#   --kind CLUSTER  load into a local kind cluster for tests; recorded by tag in ~/.cache/sdlc-k8s/images-kind.env
#                   (used by tests/k8s_kind_test.sh; needs SDLC_K8S_ALLOW_TAG=1).
# Rebuild after docker/build.sh or after new projects were warmed (the cache volume changed). A new image
# changes harness_sha, which invalidates harness-bound records, as for the docker images.
set -euo pipefail
MODE="${1:-}"; KIND_CLUSTER="${2:-}"
case "$MODE" in --push) ;; --kind) [ -n "$KIND_CLUSTER" ] || { echo "k8s/build.sh: --kind needs a cluster name" >&2; exit 2; } ;;
  *) sed -n '2,14p' "$0"; exit 2 ;; esac
CTX="$(docker context show 2>/dev/null || echo default)"
IDS="${SDLC_DOCKER_STATE:-${XDG_CACHE_HOME:-$HOME/.cache}/sdlc-docker/$CTX}/images.env"
GATE="$(sed -n 's/^SDLC_DOCKER_ID_GATE=//p' "$IDS" 2>/dev/null)"
[ -n "$GATE" ] || { echo "k8s/build.sh: no sdlc-gate image ID in $IDS (run docker/build.sh first)" >&2; exit 1; }
VOLUME="${SDLC_DOCKER_VOLUME:-sdlc-gradle-ro}"
STATE="${SDLC_K8S_STATE:-${XDG_CACHE_HOME:-$HOME/.cache}/sdlc-k8s}"; mkdir -p "$STATE"
CTXDIR="$(mktemp -d "${TMPDIR:-/tmp}/sdlc-k8s-build-XXXXXX")"; trap 'rm -rf "$CTXDIR"' EXIT

echo "k8s/build.sh: exporting the Gradle cache volume $VOLUME"
mkdir -p "$CTXDIR/gradle-ro"
docker run --rm --network none -v "$VOLUME:/v:ro" --entrypoint tar "$GATE" -C /v -cf - . | tar -C "$CTXDIR/gradle-ro" -xf -
CACHE_SHA="$(cd "$CTXDIR/gradle-ro" && find . -type f -print0 | sort -z | xargs -0 shasum -a 256 | shasum -a 256 | cut -c1-12)"
TAG="$(printf '%s' "$GATE" | sed 's/^sha256://' | cut -c1-12)-$CACHE_SHA"
# BuildKit cannot take a bare image ID in FROM: tag the pinned gate image by its ID first.
BASE="sdlc-gate-k8s-base:$(printf '%s' "$GATE" | sed 's/^sha256://' | cut -c1-12)"
docker tag "$GATE" "$BASE"
cat > "$CTXDIR/Dockerfile" <<EOF
# sdlc-gate-warm: sdlc-gate plus the offline Gradle dependency cache, for the k8s code backend.
FROM $BASE
COPY --chown=10001:10001 gradle-ro/ /opt/gradle-ro/
EOF
REPO="${SDLC_K8S_REGISTRY:-registry.example.com}/${SDLC_K8S_IMAGE_NAME:-sdlc-gate-warm}"
docker build -q -t "$REPO:$TAG" "$CTXDIR" > /dev/null
echo "k8s/build.sh: built $REPO:$TAG ($(docker image inspect "$REPO:$TAG" --format '{{.Architecture}}'), cache $CACHE_SHA)"
if [ "$MODE" = --push ]; then
  docker push -q "$REPO:$TAG" > /dev/null
  REF="$(docker image inspect "$REPO:$TAG" --format '{{range .RepoDigests}}{{println .}}{{end}}' | grep "^$REPO@" | head -1)"
  [ -n "$REF" ] || { echo "k8s/build.sh: pushed, but no repo digest recorded for $REPO:$TAG" >&2; exit 1; }
else
  kind load docker-image "$REPO:$TAG" --name "$KIND_CLUSTER" > /dev/null
  REF="$REPO:$TAG"
fi
# One record per destination: the cluster image (images.env, read by run.py) and the kind test image
# (images-kind.env, read by tests/k8s_kind_test.sh), so loading into kind never replaces the pushed digest.
REC="$STATE/images.env"; [ "$MODE" = --push ] || REC="$STATE/images-kind.env"
printf 'SDLC_K8S_IMAGE=%s\n' "$REF" > "$REC.tmp" && mv "$REC.tmp" "$REC"
echo "k8s/build.sh: recorded $REF in $REC"
