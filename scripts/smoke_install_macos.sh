#!/usr/bin/env bash
# Runs install.sh end-to-end directly on a macOS host: there's no container
# runtime to isolate this in (unlike scripts/smoke_install.sh's Linux distro
# images), so this is meant for an ephemeral macOS CI runner, not a
# developer's own machine — it installs Homebrew, MariaDB, PostgreSQL and
# Node for real.
#
# Usage: scripts/smoke_install_macos.sh
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CURRENT_BRANCH="$(git -C "$REPO_ROOT" rev-parse --abbrev-ref HEAD)"
export PILOT_REPO_URL="file://$REPO_ROOT"
export PILOT_BRANCH="$CURRENT_BRANCH"

echo "=== macOS smoke test ==="
sh "$REPO_ROOT/install.sh"

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
