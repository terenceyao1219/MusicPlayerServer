#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
APP_VERSION="$(tr -d '\r\n' < "$ROOT/VERSION")"
if [[ ! "$APP_VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+(-[A-Za-z0-9]+([.-][A-Za-z0-9]+)*)?$ ]] || (( ${#APP_VERSION} > 128 )); then
  echo "VERSION must contain a Docker-compatible version such as 1.0.0 or 1.0.0-rc.1" >&2
  exit 1
fi
# Windows credential helpers cannot run with some WSL network mounts as cwd.
# Compose paths remain anchored to the absolute compose file below.
export APP_VERSION
cd /
exec docker compose -f "$ROOT/docker/compose.yaml" "$@"
