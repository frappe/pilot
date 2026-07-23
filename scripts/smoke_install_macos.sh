#!/usr/bin/env bash
# Runs install.sh end-to-end directly on a macOS host: there's no container
# runtime to isolate this in (unlike scripts/smoke_install.sh's Linux distro
# images), so this is meant for an ephemeral macOS CI runner, not a
# developer's own machine — it installs Homebrew, MariaDB, PostgreSQL and
# Node for real.
#
# The working tree (including uncommitted changes) is committed into a
# throwaway git repo on a hardcoded "main" branch and cloned from there, the
# same way scripts/smoke_install.sh does — a checkout in CI is detached HEAD,
# so `git rev-parse --abbrev-ref HEAD` would otherwise hand install.sh the
# literal branch name "HEAD".
#
# Usage: scripts/smoke_install_macos.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

WORK_DIR="$(mktemp -d)"
trap 'rm -rf "$WORK_DIR"' EXIT

mkdir "$WORK_DIR/src"
tar -C "$REPO_ROOT" --exclude .git --exclude node_modules --exclude .admin-venv \
    -cf - . | tar -C "$WORK_DIR/src" -xf -
git -C "$WORK_DIR/src" init --quiet --initial-branch main
git -C "$WORK_DIR/src" add --all
git -C "$WORK_DIR/src" -c user.email=smoke@test -c user.name=smoke \
    commit --quiet --message "smoke test snapshot"

export PILOT_REPO_URL="file://$WORK_DIR/src"
export PILOT_BRANCH="main"

echo "=== macOS smoke test ==="
# --dev clones the mounted source checkout; the default path would fetch a
# release tarball, which does not exist for an unreleased commit under test.
sh "$REPO_ROOT/install.sh" --dev

echo "--- assertions ---"
test -x "$HOME/pilot/bench"
test -f "$HOME/pilot/.admin-venv/bin/python"
command -v brew
command -v node
command -v git
grep -qF pilot "$HOME/.zshrc" 2>/dev/null || \
    grep -qF pilot "$HOME/.profile" 2>/dev/null || \
    grep -qF pilot "$HOME/.bashrc" 2>/dev/null
echo "--- OK ---"
