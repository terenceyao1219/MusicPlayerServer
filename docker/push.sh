#!/usr/bin/env bash
set -euo pipefail

# Override these defaults with environment variables for your own registry.
REGISTRY="${REGISTRY:-as5304t-b8ed:5000}"
NAMESPACE="${NAMESPACE-workspace}"  # Set to empty to omit the namespace.
IMAGE_NAME="${IMAGE_NAME:-music-player-server}"
LOCAL_IMAGE="${LOCAL_IMAGE:-music-player-server}"

if (( $# > 1 )) || { (( $# == 1 )) && [[ "$1" != "--dry-run" ]]; }; then
  echo "Usage: bash docker/push.sh [--dry-run]" >&2
  exit 1
fi
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_VERSION="$(tr -d '\r\n' < "$ROOT/VERSION")"
if [[ ! "$APP_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9]+([.-][A-Za-z0-9]+)*)?$ ]] || (( ${#APP_VERSION} > 128 )); then
  echo "VERSION must contain a Docker-compatible version such as 1.0.0 or 1.0.0-rc.1" >&2
  exit 1
fi
if [[ "$REGISTRY" == */* || "$REGISTRY" == *[[:space:]]* || "$REGISTRY" == -* ]]; then
  echo "REGISTRY must be a hostname with optional port, without a URL scheme or path" >&2
  exit 1
fi
SOURCE="$LOCAL_IMAGE:$APP_VERSION"
TARGET="$REGISTRY/${NAMESPACE:+$NAMESPACE/}$IMAGE_NAME:$APP_VERSION"
printf 'Source: %s\nTarget: %s\n' "$SOURCE" "$TARGET"

# Avoid Windows credential helper failures on WSL network mounts.
cd /
if [[ "${1:-}" == "--dry-run" ]]; then
  printf 'docker tag %q %q\n' "$SOURCE" "$TARGET"
  printf 'docker push %q\n' "$TARGET"
  exit 0
fi

# Do not publish an image whose embedded version differs from VERSION.
IMAGE_VERSION="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.version" }}' "$SOURCE")"
if [[ "$IMAGE_VERSION" != "$APP_VERSION" ]]; then
  echo "Image version does not match VERSION. Build first: bash docker/compose.sh build" >&2
  exit 1
fi
docker tag "$SOURCE" "$TARGET"
docker push "$TARGET"
