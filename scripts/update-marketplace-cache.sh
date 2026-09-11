#!/usr/bin/env bash
set -euo pipefail

PILOT_ROOT="${1:-/home/frappe/pilot}"
CACHE_DIR="$PILOT_ROOT/system/registry-cache"

if [[ ! -d "$CACHE_DIR/.git" ]]; then
  echo "registry cache not found at: $CACHE_DIR" >&2
  exit 1
fi

cd "$CACHE_DIR"

echo "[1/3] Fetch latest registry from GitHub..."
git fetch --depth=1 origin HEAD

echo "[2/3] Reset local cache to fetched HEAD..."
git reset --hard FETCH_HEAD

echo "[3/3] Verify local == remote..."
local_sha="$(git rev-parse HEAD)"
remote_sha="$(git ls-remote https://github.com/frappe/marketplace HEAD | awk '{print $1}')"

echo "local:  $local_sha"
echo "remote: $remote_sha"

if [[ "$local_sha" != "$remote_sha" ]]; then
  echo "warning: local cache does not match remote HEAD (network lag or mirror delay)" >&2
  exit 2
fi

echo "marketplace cache is up to date"
