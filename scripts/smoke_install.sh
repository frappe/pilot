#!/usr/bin/env bash
# Runs install.sh end-to-end inside a distro container: the root path (bench
# user creation) followed by the user path (clone, uv, node, admin venv).
#
# Usage: scripts/smoke_install.sh <image>
#   e.g. scripts/smoke_install.sh debian:bookworm
#
# The working tree (including uncommitted changes) is committed into a
# throwaway git repo and mounted read-only, so install.sh clones exactly what
# you are testing.
set -euo pipefail

IMAGE="${1:?usage: $0 <image>}"
REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

mkdir "$WORK_DIR/src"
tar -C "$REPO_ROOT" --exclude .git --exclude node_modules --exclude .admin-venv \
    --exclude test-bench -cf - . | tar -C "$WORK_DIR/src" -xf -
git -C "$WORK_DIR/src" init --quiet --initial-branch main
git -C "$WORK_DIR/src" add --all
git -C "$WORK_DIR/src" -c user.email=smoke@test -c user.name=smoke \
    commit --quiet --message "smoke test snapshot"

echo "=== smoke test: $IMAGE ==="
# -i attaches stdin so the container shell actually reads the heredoc.
docker run --rm -i -v "$WORK_DIR/src:/pilot-src:ro" "$IMAGE" sh -s <<'CONTAINER' | tee "$WORK_DIR/log"
set -e
export PILOT_REPO_URL="file:///pilot-src"
export PILOT_BRANCH="main"
export BENCH_YES=1

# Parallel downloads can saturate the link and starve DNS on slow hosts;
# serialize them for a deterministic test run.
[ -f /etc/pacman.conf ] && \
    sed -i 's/^ParallelDownloads.*/ParallelDownloads = 1/' /etc/pacman.conf

echo "--- path A: root run creates the bench user ---"
sh /pilot-src/install.sh

id frappe
test -f /etc/sudoers.d/frappe
visudo -cf /etc/sudoers.d/frappe

# The mounted repo is owned by the host uid; let git clone it regardless.
git config --system safe.directory '*' 2>/dev/null || \
    git config --global safe.directory '*'

echo "--- path B: bench user run installs everything ---"
su - frappe -c "PILOT_REPO_URL=$PILOT_REPO_URL PILOT_BRANCH=$PILOT_BRANCH BENCH_YES=1 sh /pilot-src/install.sh"

echo "--- assertions ---"
su - frappe -c 'test -x "$HOME/pilot/bench"'
su - frappe -c 'test -f "$HOME/pilot/.admin-venv/bin/python"'
su - frappe -c 'command -v node'
su - frappe -c 'command -v git'
su - frappe -c 'grep -q pilot "$HOME/.bashrc" "$HOME/.profile" 2>/dev/null'
echo "--- OK ---"
CONTAINER

# Exit code alone can lie (e.g. a shell that never read the script); demand
# proof that the assertions actually ran.
grep -q -- "--- OK ---" "$WORK_DIR/log"
echo "=== $IMAGE passed ==="
