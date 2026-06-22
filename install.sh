#!/bin/sh
set -e

# POSIX sh (not bash) so a bare Alpine box can bootstrap via `wget -qO- ... | sh`
# before bash exists. The Ubuntu one-liner still pipes to bash explicitly.

# ── configuration ────────────────────────────────────────────────────────────
INSTALL_URL="https://raw.githubusercontent.com/frappe/bench-cli/main/install.sh"
REPO_URL="https://github.com/frappe/bench-cli"
BRANCH_NAME="main"
BENCH_CLI_DIR="$HOME/bench-cli"
DEFAULT_USER="frappe"

# ── arguments / environment ──────────────────────────────────────────────────
BENCH_USER="${BENCH_USER:-$DEFAULT_USER}"
# Non-interactive support: -y/--yes (or BENCH_YES=1) never prompts; the sudo
# password may be supplied with --sudo-password or the SUDO_PASS env var.
NONINTERACTIVE="${BENCH_YES:-0}"
SUDO_PASS="${SUDO_PASS:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --user) BENCH_USER="$2"; shift 2 ;;
        --user=*) BENCH_USER="${1#*=}"; shift ;;
        -y|--yes) NONINTERACTIVE=1; shift ;;
        --sudo-password|--sudo-pass) SUDO_PASS="$2"; shift 2 ;;
        --sudo-password=*|--sudo-pass=*) SUDO_PASS="${1#*=}"; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# If a sudo password was supplied, we can run unattended.
[ -n "$SUDO_PASS" ] && NONINTERACTIVE=1

# A tty is required to prompt; without one we must run non-interactively.
[ -e /dev/tty ] || NONINTERACTIVE=1

# ── Alpine bootstrap ──────────────────────────────────────────────────────────
# Bare Alpine ships almost nothing and uses apk + musl. Install the tools the
# rest of this script and bench need before they're first used: git/curl/bash,
# sudo + shadow (so the user-setup path's useradd/usermod/visudo work), a Python,
# and the base build deps for compiling the admin venv (psutil) and frappe wheels.
if [ -f /etc/alpine-release ] && command -v apk >/dev/null 2>&1; then
    echo "Alpine detected — installing base dependencies via apk..."
    if [ "$(id -u)" -eq 0 ]; then
        APK_SUDO=""
    elif command -v sudo >/dev/null 2>&1; then
        APK_SUDO="sudo"
    else
        # Bare Alpine ships no sudo, and a non-root user can't run apk. Re-run as
        # root first — that path installs sudo and prepares the bench user — then
        # run the installer again as that user.
        echo "sudo is not installed and you are not root, so apk packages cannot be"
        echo "installed. Re-run this installer as root first, then as the bench user:"
        echo ""
        echo "   wget -qO- $INSTALL_URL | sh   # as root"
        exit 1
    fi
    $APK_SUDO apk add --no-cache \
        git curl bash sudo shadow python3 python3-dev build-base linux-headers tzdata
fi

# ── sudo wrapper ──────────────────────────────────────────────────────────────
# Injects SUDO_PASS when provided so the script works unattended; otherwise
# relies on cached/passwordless sudo.
run_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
    elif [ -n "$SUDO_PASS" ]; then
        echo "$SUDO_PASS" | sudo -S "$@"
    else
        sudo "$@"
    fi
}

# ── passwordless sudo configuration ──────────────────────────────────────────
# Writes /etc/sudoers.d/<user> granting passwordless sudo. Validated with visudo
# before being installed so a bad file can never lock anyone out.
write_sudoers() {
    local user="$1"
    local file="/etc/sudoers.d/$user"
    local tmp
    tmp="$(mktemp)"

    printf '# Frappe bench — managed by install.sh, do not edit\n%s ALL=(ALL) NOPASSWD: ALL\n' "$user" > "$tmp"

    if run_sudo visudo -cf "$tmp" >/dev/null; then
        run_sudo install -m 0440 "$tmp" "$file"
        echo "Configured passwordless sudo at $file"
    else
        echo "Generated sudoers file is invalid — aborting."
        rm -f "$tmp"
        exit 1
    fi
    rm -f "$tmp"
}

# ── Path A: running as root → create the bench user, then stop ───────────────
# We do NOT switch users on the fly. We prepare the account and ask the operator
# to re-run the installer as that user.
if [ "$(id -u)" -eq 0 ]; then
    echo "Running as root. Preparing the '$BENCH_USER' user for bench..."

    if ! id "$BENCH_USER" >/dev/null 2>&1; then
        echo "Creating user '$BENCH_USER'..."
        useradd -m -s /bin/bash "$BENCH_USER"
        usermod -aG sudo "$BENCH_USER" 2>/dev/null || true
    fi

    write_sudoers "$BENCH_USER"

    echo ""
    echo "========================================================================"
    echo " User '$BENCH_USER' is ready with passwordless sudo."
    echo ""
    echo " bench must NOT be installed as root. Switch to '$BENCH_USER' and run"
    echo " the installer again:"
    echo ""
    echo "   su - $BENCH_USER"
    echo "   curl -fsSL $INSTALL_URL | bash"
    echo "========================================================================"
    exit 0
fi

# ── Path B: running as a normal user → configure sudo, then install ──────────
# Establish a usable sudo credential. Order of preference:
#   1. passwordless sudo already configured        → nothing to do
#   2. SUDO_PASS provided (unattended)             → validate it
#   3. interactive terminal                        → prompt, with retries
# We read the prompt from /dev/tty so it still works when the script is piped
# (curl ... | bash), where stdin is the script rather than the keyboard.
authenticate_sudo() {
    if sudo -n true 2>/dev/null; then
        return 0  # passwordless sudo already configured
    fi

    if [ -n "$SUDO_PASS" ]; then
        if echo "$SUDO_PASS" | sudo -S -v 2>/dev/null; then
            return 0
        fi
        echo "sudo authentication failed with the supplied password."
        exit 1
    fi

    if [ "$NONINTERACTIVE" = "1" ]; then
        echo "Passwordless sudo is required but not configured, and no password"
        echo "was supplied. Re-run with --sudo-password or configure sudo first."
        exit 1
    fi

    local pass
    while true; do
        printf "[sudo] password for %s: " "$(id -un)" > /dev/tty
        # `read -s` is a bashism; toggle echo via stty so this works under ash too.
        stty -echo < /dev/tty 2>/dev/null || true
        read -r pass < /dev/tty
        stty echo < /dev/tty 2>/dev/null || true
        echo > /dev/tty
        if echo "$pass" | sudo -S -v 2>/dev/null; then
            SUDO_PASS="$pass"
            return 0
        fi
        echo "Sorry, try again." > /dev/tty
    done
}

if ! command -v sudo >/dev/null 2>&1; then
    echo "sudo is required but not installed — aborting."
    exit 1
fi

echo "Bench needs passwordless sudo to install packages and manage services."
if [ "$(uname)" = "Darwin" ]; then
    echo ""
    echo "NOTE: this will grant the current user '$(id -un)' passwordless sudo"
    echo "      access by writing /etc/sudoers.d/$(id -un)."
    echo ""
fi
authenticate_sudo
write_sudoers "$(id -un)"

# Clone or update the repo
if [ -d "$BENCH_CLI_DIR" ]; then
    echo "Updating bench-cli..."
    git -C "$BENCH_CLI_DIR" pull
else
    echo "Cloning bench-cli ($BRANCH_NAME branch)..."
    git clone -b "$BRANCH_NAME" "$REPO_URL" "$BENCH_CLI_DIR"
fi

chmod +x "$BENCH_CLI_DIR/bench"

# Install uv if not present
if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# Install Node.js
if ! command -v node >/dev/null 2>&1 && command -v apt-get >/dev/null 2>&1; then
    echo "Installing Node.js..."
    NODE_SETUP_TMP="$(mktemp)"
    curl -fsSL https://deb.nodesource.com/setup_24.x -o "$NODE_SETUP_TMP"
    run_sudo bash "$NODE_SETUP_TMP"
    rm -f "$NODE_SETUP_TMP"
    run_sudo apt-get install -y nodejs
fi

# Install timezone data (required by Python's zoneinfo module on systems without system tzdata)
if [ "$(uname)" != "Darwin" ]; then
    if command -v apt-get >/dev/null 2>&1; then
        if ! dpkg -l tzdata 2>/dev/null | grep -q '^ii'; then
            echo "Installing timezone data..."
            DEBIAN_FRONTEND=noninteractive run_sudo apt-get install -y tzdata
        fi
    elif command -v apk >/dev/null 2>&1; then
        if ! apk info -e tzdata >/dev/null 2>&1; then
            echo "Installing timezone data..."
            run_sudo apk add --no-cache tzdata
        fi
    fi
fi

# ── add bench to PATH ─────────────────────────────────────────────────────────
add_to_path() {
    local rc="$1"
    local line="export PATH=\"\$HOME/bench-cli:\$PATH\""
    if ! grep -qF 'bench-cli' "$rc" 2>/dev/null; then
        echo "$line" >> "$rc"
        echo "Added bench to PATH in $rc"
    fi
}

RC_FILE=""
case "$SHELL" in
    */fish)
        FISH_CONFIG="$HOME/.config/fish/config.fish"
        mkdir -p "$(dirname "$FISH_CONFIG")"
        if ! grep -qF 'bench-cli' "$FISH_CONFIG" 2>/dev/null; then
            echo "fish_add_path \$HOME/bench-cli" >> "$FISH_CONFIG"
            echo "Added bench to PATH in $FISH_CONFIG"
        fi
        ;;
    */zsh)
        RC_FILE="$HOME/.zshrc"
        add_to_path "$RC_FILE"
        ;;
    */ash|*/sh|"")
        # Alpine/POSIX login shells read ~/.profile.
        RC_FILE="$HOME/.profile"
        add_to_path "$RC_FILE"
        ;;
    *)
        RC_FILE="$HOME/.bashrc"
        add_to_path "$RC_FILE"
        ;;
esac

export PATH="$BENCH_CLI_DIR:$PATH"

# Best-effort: load the updated rc into this session. Shell-specific syntax (or a
# zsh rc sourced under bash) may fail — that's fine, the PATH export above already
# applies and a new terminal picks up the rc.
if [ -n "$RC_FILE" ] && [ -f "$RC_FILE" ]; then
    # shellcheck disable=SC1090
    . "$RC_FILE" 2>/dev/null || true
fi

# ── admin venv ────────────────────────────────────────────────────────────────
ADMIN_VENV="$BENCH_CLI_DIR/.admin-venv"
if [ ! -f "$ADMIN_VENV/bin/python" ]; then
    echo "Setting up admin environment..."
    uv venv "$ADMIN_VENV" --quiet
    if command -v python3 >/dev/null 2>&1; then
        ADMIN_DEPS=$(python3 -c "
import tomllib, sys
with open('$BENCH_CLI_DIR/pyproject.toml', 'rb') as f:
    d = tomllib.load(f)
deps = d.get('project', {}).get('optional-dependencies', {}).get('admin', [])
print(' '.join(deps))
" 2>/dev/null)
    fi
    if [ -z "$ADMIN_DEPS" ]; then
        ADMIN_DEPS="flask>=3.0 psutil>=5.9 pymysql>=1.1 gunicorn>=21.2"
    fi
    uv pip install --python "$ADMIN_VENV/bin/python" --quiet $ADMIN_DEPS
    echo "Admin environment ready."
fi

echo ""
echo "bench installed to $BENCH_CLI_DIR"
echo ""
echo "Quick start:"
echo "  bench new my-bench"
echo "  bench start"
echo ""
echo "If 'bench' is not found, open a new terminal or run: . ${RC_FILE:-$HOME/.bashrc}"
