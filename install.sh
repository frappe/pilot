#!/bin/sh
set -e

# POSIX sh (not bash) so a bare box can bootstrap via `wget -qO- ... | sh`
# before bash exists. The provisioned-host one-liner still pipes to bash.
#
# Supported distros: Debian, Ubuntu, Fedora, Arch (and their derivatives via
# ID_LIKE). Unknown distros fall back to apt when available.

# ── configuration ────────────────────────────────────────────────────────────
INSTALL_URL="https://raw.githubusercontent.com/frappe/pilot/main/install.sh"
# Overridable so smoke tests can install from a local checkout.
REPO_URL="${PILOT_REPO_URL:-https://github.com/frappe/pilot}"
BRANCH_NAME="${PILOT_BRANCH:-main}"
PILOT_DIR="$HOME/pilot"
DEFAULT_USER="frappe"

# ── arguments / environment ──────────────────────────────────────────────────
BENCH_USER="${BENCH_USER:-$DEFAULT_USER}"
# Only relevant to the rare case of running this script directly as a
# pre-existing non-root sudo user with a base tool missing (bootstrap_needed):
# that fallback still shells out to sudo, and unattended runs (e.g. CI) can't
# answer its password prompt.
SUDO_PASS="${SUDO_PASS:-}"
# Default install pulls a prebuilt release tarball; --dev git-clones main and
# compiles the admin frontend from source (for contributors).
DEV_MODE="${PILOT_DEV:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --user) BENCH_USER="$2"; shift 2 ;;
        --user=*) BENCH_USER="${1#*=}"; shift ;;
        --sudo-password|--sudo-pass) SUDO_PASS="$2"; shift 2 ;;
        --sudo-password=*|--sudo-pass=*) SUDO_PASS="${1#*=}"; shift ;;
        --dev) DEV_MODE=1; shift ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# ── distro detection ──────────────────────────────────────────────────────────
detect_distro() {
    if [ "$(uname)" = "Darwin" ]; then
        echo macos
        return
    fi
    if [ ! -r /etc/os-release ]; then
        echo unknown
        return
    fi
    # os-release is sh syntax by spec; source it in subshells so its variables
    # never leak into ours.
    # shellcheck disable=SC1091
    distro_id=$(. /etc/os-release; echo "$ID")
    # shellcheck disable=SC1091
    distro_like=$(. /etc/os-release; echo "${ID_LIKE:-}")
    case "$distro_id" in
        debian|ubuntu|fedora|arch) echo "$distro_id"; return ;;
    esac
    # Derivatives advertise their parent in ID_LIKE (e.g. Mint -> ubuntu).
    for token in $distro_like; do
        case "$token" in
            debian|ubuntu|fedora|arch) echo "$token"; return ;;
        esac
    done
    echo unknown
}

DISTRO="$(detect_distro)"

# ── sudo wrapper ──────────────────────────────────────────────────────────────
# Injects SUDO_PASS when provided so the script works unattended. Otherwise,
# if sudo would actually need a password (no cached/passwordless sudo), we
# prompt for it ourselves via /dev/tty and cache the answer in SUDO_PASS —
# piping this script through `curl | sh` leaves stdin occupied by the script
# itself, so sudo's own prompt can't read an answer from it.
run_sudo() {
    if [ "$(id -u)" -eq 0 ]; then
        "$@"
        return
    fi
    if [ -z "$SUDO_PASS" ] && ! sudo -n true 2>/dev/null; then
        if [ -r /dev/tty ]; then
            printf "[sudo] password for %s: " "$(id -un)" > /dev/tty
            stty -echo < /dev/tty 2>/dev/null
            read -r SUDO_PASS < /dev/tty
            stty echo < /dev/tty 2>/dev/null
            printf "\n" > /dev/tty
        fi
    fi
    if [ -n "$SUDO_PASS" ]; then
        echo "$SUDO_PASS" | sudo -S "$@"
    else
        sudo "$@"
    fi
}

# Downloads a vendor bootstrap script (MariaDB repo setup, NodeSource,
# Homebrew) over a pinned HTTPS/TLS floor. These vendors only publish
# "curl | bash" installers with no checksum/signature to pin against, so the
# content itself is trusted the same way their own docs instruct — but a
# truncated transfer, non-HTTPS redirect, or empty/garbage response is caught
# before anything runs.
download_installer() {
    url="$1"
    tmp="$(mktemp)"
    curl -fsSL --proto '=https' --tlsv1.2 "$url" -o "$tmp"
    if [ ! -s "$tmp" ] || ! head -c 2 "$tmp" | grep -q '^#'; then
        echo "Downloaded installer from $url looks invalid, aborting." >&2
        rm -f "$tmp"
        exit 1
    fi
    echo "$tmp"
}

fetch_and_run_as_root() {
    url="$1"; shift
    tmp="$(download_installer "$url")" || exit 1
    run_sudo bash "$tmp" "$@"
    rm -f "$tmp"
}

# ── package manager primitives ────────────────────────────────────────────────
# Unknown distros fall back to apt, mirroring the runtime in pilot/platform.py.
# macOS uses Homebrew, ownership-per-user, so it never goes through run_sudo.
pkg_update() {
    case "$DISTRO" in
        macos)  ensure_homebrew; brew update ;;
        fedora) run_sudo dnf -y makecache ;;
        arch)   run_sudo pacman -Sy --noconfirm --disable-download-timeout ;;
        *)      run_sudo apt-get update ;;
    esac
}

pkg_install() {
    case "$DISTRO" in
        macos)  ensure_homebrew; brew install "$@" ;;
        # --allowerasing: containers ship curl-minimal, which conflicts with curl.
        fedora) run_sudo dnf install -y --allowerasing "$@" ;;
        # -Sy: sync the package database first; stale Arch mirrors 404 otherwise.
        # The download timeout aborts large transfers on slow links; disable it.
        arch)   run_sudo pacman -Sy --noconfirm --needed --disable-download-timeout "$@" ;;
        *)      run_sudo env DEBIAN_FRONTEND=noninteractive apt-get install -y "$@" ;;
    esac
}

pkg_installed() {
    case "$DISTRO" in
        macos)  brew list --versions "$1" >/dev/null 2>&1 ;;
        fedora) rpm -q "$1" >/dev/null 2>&1 ;;
        arch)   pacman -Qi "$1" >/dev/null 2>&1 ;;
        *)      dpkg -l "$1" 2>/dev/null | grep -q '^ii' ;;
    esac
}

# download_installer (mariadb/nodesource/homebrew) needs curl before anything
# else runs, and bootstrap_packages()'s own curl install happens too late for
# that — so get it on its own, ahead of everything else in bootstrap().
# macOS always ships curl, so this is a no-op there.
ensure_curl() {
    command -v curl >/dev/null 2>&1 && return 0
    [ "$DISTRO" = "macos" ] && return 0
    echo "Installing curl..."
    pkg_update
    pkg_install curl
}

# Homebrew is the one base dependency on macOS the runtime can't lazily
# install itself (pilot/package_managers.py's BrewPackageManager assumes
# `brew` already exists). On Intel Macs, Homebrew's own installer needs sudo
# for the initial /usr/local setup; priming the sudo timestamp cache first
# means it reuses that instead of prompting separately (or failing outright
# if --sudo-password was given but the installer's own prompt can't read it).
ensure_homebrew() {
    command -v brew >/dev/null 2>&1 && return 0
    echo "Installing Homebrew..."
    run_sudo true
    tmp="$(download_installer "https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh")" || exit 1
    NONINTERACTIVE=1 bash "$tmp"
    rm -f "$tmp"
    if [ -x /opt/homebrew/bin/brew ]; then
        eval "$(/opt/homebrew/bin/brew shellenv)"
    elif [ -x /usr/local/bin/brew ]; then
        eval "$(/usr/local/bin/brew shellenv)"
    fi
}

# ── base dependency bootstrap ─────────────────────────────────────────────────
# Bare images of most distros ship almost nothing. Install the tools the rest
# of this script and bench need before they're first used: git/curl/bash, sudo
# + user tooling (so the user-setup path's useradd/usermod/visudo work), a
# Python, and the base build deps for compiling the admin venv (psutil) and
# frappe wheels. macOS ships curl/bash/sudo itself, so brew (the one thing it
# can't lazily install for itself) takes their place here.
bootstrap_needed() {
    if [ "$DISTRO" = "macos" ]; then
        tools="git brew python3"
    else
        tools="git curl bash sudo python3"
    fi
    for tool in $tools; do
        command -v "$tool" >/dev/null 2>&1 || return 0
    done
    return 1
}

bootstrap_packages() {
    case "$DISTRO" in
        macos)
            pkg_install git python3 ;;
        debian|ubuntu)
            pkg_install git curl bash sudo ca-certificates python3 python3-dev build-essential tzdata ;;
        fedora)
            pkg_install git curl bash sudo shadow-utils python3 python3-devel gcc gcc-c++ make tzdata ;;
        arch)
            pkg_install git curl bash sudo python base-devel tzdata ;;
    esac
}

# ── database engines ──────────────────────────────────────────────────────────
# bench runs one MariaDB server and one PostgreSQL server per bench user
# (rootless, systemctl --user) shared across that user's benches, so the
# engines must already be installed system-wide before `bench init` ever
# runs — the runtime never installs packages itself, and the bench user has
# no privileges to. Root, one-time.
_MARIADB_REPO_SETUP_URL="https://r.mariadb.com/downloads/mariadb_repo_setup"
_MARIADB_VERSION="11.8"
_POSTGRES_VERSION="16"

# Debian/Ubuntu ship an older MariaDB than 11.8 by default, so the official
# repo must be added before bootstrap()'s single pkg_update() runs — that one
# call then refreshes both the base indices and this new repo together,
# instead of a second apt-get update just for it.
add_distro_repos() {
    case "$DISTRO" in
        debian|ubuntu)
            # Same version the runtime expects (MariaDBManager DEFAULT_VERSION).
            fetch_and_run_as_root "$_MARIADB_REPO_SETUP_URL" --mariadb-server-version="mariadb-$_MARIADB_VERSION" ;;
    esac
}

install_database_engines() {
    # Dev headers for building the Python client libraries (mysqlclient,
    # psycopg) that frappe's virtualenv compiles during `bench init` — listed
    # here so bench init never has to install a package itself.
    case "$DISTRO" in
        macos)
            # Versions pinned to match the runtime's own defaults
            # (MariaDBManager/PostgresManager _DEFAULT_VERSION), so the formula
            # this installs is the same one BrewPackageManager would lazily
            # reach for later.
            pkg_install "mariadb@$_MARIADB_VERSION" "postgresql@$_POSTGRES_VERSION" redis ;;
        debian|ubuntu)
            pkg_install mariadb-server mariadb-client libmariadb-dev postgresql postgresql-client libpq-dev pkg-config redis-server ;;
        fedora)
            # Fedora 41+ ships valkey in place of redis (same alias the runtime uses).
            pkg_install mariadb-server mariadb mariadb-connector-c-devel postgresql-server postgresql libpq-devel pkgconf-pkg-config valkey ;;
        arch)
            pkg_install mariadb mariadb-clients mariadb-libs postgresql postgresql-libs pkgconf redis ;;
    esac
}

# ── production stack ──────────────────────────────────────────────────────────
# nginx, certbot and supervisor are what `bench setup production` needs. Same
# reasoning as the database engines: installed here, as root, so deploying a
# bench never has to install a package as the bench user — which cannot,
# by design.
install_production_packages() {
    case "$DISTRO" in
        macos)  pkg_install nginx certbot ;;
        debian|ubuntu)
            # libnginx-mod-http-modsecurity is the WAF module; installing it
            # up front keeps enabling the WAF later a non-root operation.
            pkg_install nginx certbot supervisor libnginx-mod-http-modsecurity ;;
        fedora) pkg_install nginx certbot supervisor ;;
        arch)   pkg_install nginx certbot supervisor ;;
    esac
}

# Distro packages auto-start/enable a system-wide service on the default
# port (3306/5432). bench never uses that — it runs a per-user instance
# instead — so free the ports right away rather than have every `bench
# init` fight over them. nginx and supervisor are stopped for the same
# reason: `bench setup production` starts nginx itself (a grant covers it),
# and benches are supervised by their own config, not the distro's.
disable_system_services() {
    case "$DISTRO" in
        macos|unknown) ;;
        *)
            for service in mariadb postgresql redis-server redis valkey nginx supervisor; do
                run_sudo systemctl disable --now "$service" 2>/dev/null || true
            done
            ;;
    esac
}

# ── Node.js ───────────────────────────────────────────────────────────────────
# System-wide, root/bootstrap only — same reasoning as the database engines:
# installed once up front so the bench user never needs privileges of its own.
# NodeSource pins Node 24 on the deb/rpm distros; Arch ships a current Node in
# its own repos.
install_node_nodesource() {
    kind="$1"; shift
    fetch_and_run_as_root "https://${kind}.nodesource.com/setup_24.x"
    run_sudo "$@"
}

install_node() {
    command -v node >/dev/null 2>&1 && return 0
    case "$DISTRO" in
        macos)  echo "Installing Node.js..."; pkg_install node ;;
        debian|ubuntu)
            echo "Installing Node.js..."
            install_node_nodesource deb apt-get install -y nodejs ;;
        fedora)
            echo "Installing Node.js..."
            install_node_nodesource rpm dnf install -y nodejs ;;
        arch)   echo "Installing Node.js..."; pkg_install nodejs npm ;;
        *)
            # Same fallback as before this script knew about distros: NodeSource
            # when apt exists, otherwise leave Node to the operator.
            if command -v apt-get >/dev/null 2>&1; then
                echo "Installing Node.js..."
                install_node_nodesource deb apt-get install -y nodejs
            fi ;;
    esac
}

bootstrap() {
    [ "$DISTRO" = "unknown" ] && return 0
    # Root always bootstraps (idempotent, and bare containers need it before
    # useradd); a normal user only when a base tool is actually missing
    # (bootstrap_needed is platform-aware: brew on macOS, sudo elsewhere).
    if [ "$(id -u)" -ne 0 ]; then
        bootstrap_needed || return 0
        if ! command -v sudo >/dev/null 2>&1; then
            # A non-root user can't install packages without sudo. Re-run as
            # root first — that path installs sudo and prepares the bench user.
            echo "sudo is not installed and you are not root, so base packages cannot"
            echo "be installed. Re-run this installer as root first, then as the bench"
            echo "user:"
            echo ""
            echo "   wget -qO- $INSTALL_URL | sh   # as root"
            exit 1
        fi
    fi
    echo "$DISTRO detected — installing base dependencies..."
    ensure_curl
    add_distro_repos
    pkg_update
    bootstrap_packages
    install_database_engines
    install_production_packages
    disable_system_services
    install_node
}

bootstrap

# The group conventionally granted admin rights, so the bench user can
# authenticate sudo interactively later if they ever need it (e.g. `bench
# setup production`). Nothing in this installer's own bench-user path needs
# sudo — base tools, database engines and Node.js are all installed
# system-wide above, before the bench user is ever created.
admin_group() {
    case "$DISTRO" in
        debian|ubuntu|unknown) echo sudo ;;
        *) echo wheel ;;
    esac
}

# Bench services run as `systemctl --user` units, so the bench user's systemd
# instance must stay alive with no login session. Enabling lingering also
# starts it, creating the D-Bus socket every `systemctl --user` call needs.
systemd_booted() {
    [ -d /run/systemd/system ] && command -v loginctl >/dev/null 2>&1
}

linger_enabled() {
    [ "$(loginctl show-user "$1" --property=Linger 2>/dev/null)" = "Linger=yes" ]
}

# nginx reads each bench's vhost from a file the bench user owns. Root drops
# the one glob that pulls them in, so publishing a vhost afterwards is an
# ordinary file write instead of a privileged symlink into /etc/nginx.
install_nginx_include() {
    bench_home="$(getent passwd "$1" | cut -d: -f6)"
    [ -n "$bench_home" ] && [ -d /etc/nginx/conf.d ] || return 0
    echo "Installing the nginx include for '$1'..."
    cat > /etc/nginx/conf.d/00-pilot.conf <<EOF
include $bench_home/pilot/nginx/*.conf;
include $bench_home/pilot/benches/*/config/nginx/include.conf;
EOF
    chmod 644 /etc/nginx/conf.d/00-pilot.conf
    # The distro's stock default site also claims default_server on :80;
    # nginx rejects a duplicate, so drop it and let a bench's vhost win.
    rm -f /etc/nginx/sites-enabled/default
    # Older installs symlinked each bench into conf.d. The glob above now
    # loads the same file, and nginx rejects the duplicate server blocks, so
    # a re-run has to clear the symlinks it supersedes.
    for link in /etc/nginx/conf.d/*.conf; do
        [ -L "$link" ] || continue
        case "$(readlink "$link")" in
            "$bench_home"/pilot/benches/*) rm -f "$link" ;;
        esac
    done
}

# logrotate refuses to read a config it doesn't own, so these can't be written
# by the bench user at runtime. One root-owned config globbing the log dirs
# covers every bench and every monitor, present and future.
install_logrotate() {
    bench_home="$(getent passwd "$1" | cut -d: -f6)"
    [ -n "$bench_home" ] && [ -d /etc/logrotate.d ] || return 0
    echo "Installing log rotation for '$1'..."
    cat > /etc/logrotate.d/pilot <<EOF
$bench_home/pilot/logs/*.log $bench_home/pilot/benches/*/logs/*.log {
    size 500M
    rotate 3
    compress
    missingok
    notifempty
    copytruncate
    su $1 $1
}
EOF
    chmod 644 /etc/logrotate.d/pilot
}

# nginx workers must run as the bench user to read its sites. Doing it here
# keeps `bench setup production` out of /etc/nginx/nginx.conf entirely.
set_nginx_worker_user() {
    conf=/etc/nginx/nginx.conf
    [ "$DISTRO" != "macos" ] && [ -f "$conf" ] || return 0
    grep -q "^[[:space:]]*user[[:space:]]\{1,\}$1;" "$conf" && return 0
    echo "Setting the nginx worker user to '$1'..."
    if grep -q "^[[:space:]]*user[[:space:]]" "$conf"; then
        sed -i "s/^[[:space:]]*user[[:space:]].*;/user $1;/" "$conf"
    else
        sed -i "1i user $1;" "$conf"
    fi
}

# ── Path A: running as root → create the bench user, then stop ───────────────
# We do NOT switch users on the fly. We prepare the account and ask the operator
# to re-run the installer as that user.
if [ "$(id -u)" -eq 0 ]; then
    echo "Running as root. Preparing the '$BENCH_USER' user for bench..."

    if ! id "$BENCH_USER" >/dev/null 2>&1; then
        echo "Creating user '$BENCH_USER'..."
        useradd -m -s /bin/bash "$BENCH_USER"
        usermod -aG "$(admin_group)" "$BENCH_USER" 2>/dev/null || true
    fi

    if systemd_booted && ! linger_enabled "$BENCH_USER"; then
        echo "Enabling systemd lingering for '$BENCH_USER'..."
        loginctl enable-linger "$BENCH_USER"
    fi

    install_nginx_include "$BENCH_USER"
    set_nginx_worker_user "$BENCH_USER"
    install_logrotate "$BENCH_USER"
    install_sudoers_grants "$BENCH_USER"

    echo ""
    echo "========================================================================"
    echo " User '$BENCH_USER' is ready — base tools and database engines are"
    echo " installed system-wide, so day-to-day bench commands never need root."
    echo ""
    echo " bench must NOT be installed as root. Switch to '$BENCH_USER' and run"
    echo " the installer again:"
    echo ""
    echo "   su - $BENCH_USER"
    echo "   curl -fsSL $INSTALL_URL | bash"
    echo "========================================================================"
    exit 0
fi

# ── Path B: running as a normal user → clone and install ─────────────────────
# All system-wide, privileged setup (base tools, database engines, Node.js)
# already happened in bootstrap() above — as root, or earlier in this same
# run if a base tool was missing — so nothing below here needs sudo.
# Lingering is normally enabled by the root pass above. If this user was
# prepared some other way, only root can turn it on — fail here rather than
# midway through `bench init`, where systemctl --user has no bus to talk to.
if systemd_booted && ! linger_enabled "$(id -un)"; then
    if ! sudo -n loginctl enable-linger "$(id -un)" 2>/dev/null; then
        echo "systemd lingering is not enabled for '$(id -un)', and this user cannot" >&2
        echo "enable it without a password. Bench services run as systemctl --user" >&2
        echo "units, which need it." >&2
        echo "" >&2
        echo "Run this as root, then re-run the installer:" >&2
        echo "" >&2
        echo "   loginctl enable-linger $(id -un)" >&2
        exit 1
    fi
fi

echo "Setting up your environment..."

# ── fetch pilot: release tarball (default) or git clone (--dev) ────────────────
# The release tarball ships the compiled admin frontend and a VERSION file;
# --dev clones the source so contributors always build the frontend locally.
install_release_tarball() {
    releases_api="https://api.github.com/repos/frappe/pilot/releases?per_page=1"
    echo "Fetching the latest pilot release..."
    asset_url=$(curl -fsSL --proto '=https' --tlsv1.2 "$releases_api" \
        | grep -o 'https://[^"]*/pilot\.tar\.gz' | head -n1)
    if [ -z "$asset_url" ]; then
        echo "Could not find a pilot.tar.gz release asset." >&2
        echo "Install a development checkout instead with: $INSTALL_URL --dev" >&2
        exit 1
    fi
    tmp="$(mktemp)"
    curl -fsSL --proto '=https' --tlsv1.2 "$asset_url" -o "$tmp"
    # tar only writes archived paths, so an existing benches/ (local data) is left intact.
    mkdir -p "$PILOT_DIR"
    tar -xzf "$tmp" -C "$PILOT_DIR"
    rm -f "$tmp"
}

if [ -n "$DEV_MODE" ]; then
    if [ -d "$PILOT_DIR/.git" ]; then
        echo "Updating pilot (dev)..."
        git -C "$PILOT_DIR" pull
    else
        echo "Cloning pilot ($BRANCH_NAME branch)..."
        git clone -b "$BRANCH_NAME" "$REPO_URL" "$PILOT_DIR"
    fi
else
    install_release_tarball
fi

chmod +x "$PILOT_DIR/bench"

# ── uv ────────────────────────────────────────────────────────────────────────
if ! command -v uv >/dev/null 2>&1; then
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
fi

# ── timezone data ─────────────────────────────────────────────────────────────
# Required by Python's zoneinfo module on systems without system tzdata.
ensure_tzdata() {
    case "$DISTRO" in
        macos) return 0 ;;
        unknown) command -v apt-get >/dev/null 2>&1 || return 0 ;;
    esac
    pkg_installed tzdata && return 0
    echo "Installing timezone data..."
    pkg_install tzdata
}

ensure_tzdata

# ── add bench to PATH ─────────────────────────────────────────────────────────
add_to_path() {
    rc="$1"
    line="export PATH=\"\$HOME/pilot:\$PATH\""
    if ! grep -qF 'pilot' "$rc" 2>/dev/null; then
        echo "$line" >> "$rc"
        echo "Added bench to PATH in $rc"
    fi
}

RC_FILE=""
case "$SHELL" in
    */fish)
        FISH_CONFIG="$HOME/.config/fish/config.fish"
        mkdir -p "$(dirname "$FISH_CONFIG")"
        if ! grep -qF 'pilot' "$FISH_CONFIG" 2>/dev/null; then
            echo "fish_add_path \$HOME/pilot" >> "$FISH_CONFIG"
            echo "Added bench to PATH in $FISH_CONFIG"
        fi
        ;;
    */zsh)
        RC_FILE="$HOME/.zshrc"
        add_to_path "$RC_FILE"
        ;;
    */ash|*/sh|"")
        # POSIX login shells read ~/.profile.
        RC_FILE="$HOME/.profile"
        add_to_path "$RC_FILE"
        ;;
    *)
        RC_FILE="$HOME/.bashrc"
        add_to_path "$RC_FILE"
        ;;
esac

export PATH="$PILOT_DIR:$PATH"

# Best-effort: load the updated rc into this session. Shell-specific syntax (or a
# zsh rc sourced under bash) may fail — that's fine, the PATH export above already
# applies and a new terminal picks up the rc.
if [ -n "$RC_FILE" ] && [ -f "$RC_FILE" ]; then
    # shellcheck disable=SC1090
    . "$RC_FILE" 2>/dev/null || true
fi

# ── admin venv ────────────────────────────────────────────────────────────────
ADMIN_VENV="$PILOT_DIR/.admin-venv"
if [ ! -f "$ADMIN_VENV/bin/python" ]; then
    echo "Setting up admin environment..."
    uv venv "$ADMIN_VENV" --quiet
    if command -v python3 >/dev/null 2>&1; then
        ADMIN_DEPS=$(python3 -c "
import tomllib, sys
with open('$PILOT_DIR/pyproject.toml', 'rb') as f:
    d = tomllib.load(f)
deps = d.get('project', {}).get('optional-dependencies', {}).get('admin', [])
print(' '.join(deps))
" 2>/dev/null)
    fi
    if [ -z "$ADMIN_DEPS" ]; then
        ADMIN_DEPS="flask>=3.0 psutil>=5.9 pymysql>=1.1 gunicorn>=21.2 pyjwt[crypto]>=2.8"
    fi
    # shellcheck disable=SC2086 # ADMIN_DEPS is a space-separated list
    uv pip install --python "$ADMIN_VENV/bin/python" --quiet $ADMIN_DEPS
    echo "Admin environment ready."
fi

echo ""
echo "bench installed to $PILOT_DIR"
echo ""
echo "Quick start:"
echo "  bench new my-bench"
echo "  bench start"
echo ""
echo "If 'bench' is not found, open a new terminal or run: . ${RC_FILE:-$HOME/.bashrc}"
