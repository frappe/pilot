#!/bin/sh
set -e

# POSIX sh (not bash) so a bare box can bootstrap via `wget -qO- ... | sh`
# before bash exists. Supported distros: Debian, Ubuntu, Fedora, Arch, and
# their derivatives via ID_LIKE. Unknown distros fall back to apt.
#
# Two passes. As root it prepares the host and the bench user, then stops.
# As that user it installs bench itself, needing no privileges at all.

# ── configuration ─────────────────────────────────────────────────────────────
# All three point at the same GitHub repo; override PILOT_GITHUB_SLUG to install
# from a fork (releases + self-reference URL follow it).
GITHUB_SLUG="${PILOT_GITHUB_SLUG:-frappe/pilot}"
INSTALL_URL="https://raw.githubusercontent.com/$GITHUB_SLUG/main/install.sh"
REPO_URL="${PILOT_REPO_URL:-https://github.com/$GITHUB_SLUG}"
BRANCH_NAME="${PILOT_BRANCH:-main}"
PILOT_DIR="$HOME/pilot"
BENCH_USER="${BENCH_USER:-frappe}"
# Lets an unattended run answer sudo, which `curl | sh` cannot prompt for.
SUDO_PASS="${SUDO_PASS:-}"
# The default install pulls a prebuilt release tarball; --dev clones main and
# compiles the admin frontend from source.
DEV_MODE="${PILOT_DEV:-}"

MARIADB_REPO_SETUP_URL="https://r.mariadb.com/downloads/mariadb_repo_setup"
# Match the runtime's own defaults (MariaDBManager/PostgresManager).
MARIADB_VERSION="11.8"
POSTGRES_VERSION="16"

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

# ── platform ──────────────────────────────────────────────────────────────────
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

is_root() {
    [ "$(id -u)" -eq 0 ]
}

# Piping this script through `curl | sh` leaves stdin occupied, so sudo's own
# prompt cannot read an answer. Ask via /dev/tty and cache it instead.
run_sudo() {
    if is_root; then
        "$@"
        return
    fi
    if [ -z "$SUDO_PASS" ] && ! sudo -n true 2>/dev/null && [ -r /dev/tty ]; then
        printf "[sudo] password for %s: " "$(id -un)" > /dev/tty
        stty -echo < /dev/tty 2>/dev/null
        read -r SUDO_PASS < /dev/tty
        stty echo < /dev/tty 2>/dev/null
        printf "\n" > /dev/tty
    fi
    if [ -n "$SUDO_PASS" ]; then
        echo "$SUDO_PASS" | sudo -S "$@"
    else
        sudo "$@"
    fi
}

# ── package manager ───────────────────────────────────────────────────────────
# Unknown distros fall back to apt, mirroring pilot/managers/packages.py.
# macOS uses Homebrew, which is per-user and never goes through run_sudo.
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
        # -Sy: stale Arch mirrors 404 otherwise. The download timeout aborts
        # large transfers on slow links, so turn it off.
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

# Vendors (MariaDB, NodeSource, Homebrew) only publish `curl | bash` installers
# with nothing to pin against, so the content is trusted the way their docs
# instruct. A truncated transfer or non-HTTPS redirect is still caught here.
download_installer() {
    tmp="$(mktemp)"
    curl -fsSL --proto '=https' --tlsv1.2 "$1" -o "$tmp"
    if [ ! -s "$tmp" ] || ! head -c 2 "$tmp" | grep -q '^#'; then
        echo "Downloaded installer from $1 looks invalid, aborting." >&2
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

# download_installer needs curl before bootstrap_packages would install it.
ensure_curl() {
    command -v curl >/dev/null 2>&1 && return 0
    [ "$DISTRO" = "macos" ] && return 0
    echo "Installing curl..."
    pkg_update
    pkg_install curl
}

# The one macOS dependency the runtime cannot install for itself. Priming the
# sudo timestamp first means Homebrew's installer reuses it on Intel Macs
# rather than prompting again, where it could not read an answer.
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

# ── system packages ───────────────────────────────────────────────────────────
# Everything bench needs at runtime is installed here, as root, once. The bench
# user has no passwordless sudo, so anything missing from this list becomes a
# password prompt in the middle of `bench init` or a deploy.

# Bare images ship almost nothing: the tools this script and bench both need
# before first use, plus the build deps for the admin venv and frappe wheels.
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

# Debian/Ubuntu ship an older MariaDB than we want, so add the official repo
# before the single pkg_update below refreshes both it and the base indices.
add_distro_repos() {
    case "$DISTRO" in
        debian|ubuntu)
            fetch_and_run_as_root "$MARIADB_REPO_SETUP_URL" \
                --mariadb-server-version="mariadb-$MARIADB_VERSION" ;;
    esac
}

# One MariaDB, PostgreSQL and Redis per bench user (rootless, systemctl --user),
# shared across that user's benches. The dev headers are for the Python client
# libraries frappe's virtualenv compiles during `bench init`.
install_database_engines() {
    case "$DISTRO" in
        macos)
            pkg_install "mariadb@$MARIADB_VERSION" "postgresql@$POSTGRES_VERSION" redis ;;
        debian|ubuntu)
            pkg_install mariadb-server mariadb-client libmariadb-dev postgresql postgresql-client libpq-dev pkg-config redis-server ;;
        fedora)
            # Fedora 41+ ships valkey in place of redis (the alias the runtime resolves).
            pkg_install mariadb-server mariadb mariadb-connector-c-devel postgresql-server postgresql libpq-devel pkgconf-pkg-config valkey ;;
        arch)
            pkg_install mariadb mariadb-clients mariadb-libs postgresql postgresql-libs pkgconf redis ;;
    esac
}

# What `bench setup production` needs. Installing the WAF module up front keeps
# enabling the WAF later a non-root operation too.
install_production_packages() {
    case "$DISTRO" in
        macos)  pkg_install nginx certbot ;;
        debian|ubuntu)
            pkg_install nginx certbot supervisor libnginx-mod-http-modsecurity ;;
        fedora) pkg_install nginx certbot supervisor ;;
        arch)   pkg_install nginx certbot supervisor ;;
    esac
}

# NodeSource pins Node 24 on deb/rpm distros; Arch ships a current Node itself.
install_node() {
    command -v node >/dev/null 2>&1 && return 0
    # An unknown distro only gets Node when apt is there to install it.
    if [ "$DISTRO" = "unknown" ] && ! command -v apt-get >/dev/null 2>&1; then
        return 0
    fi
    echo "Installing Node.js..."
    case "$DISTRO" in
        macos)  pkg_install node ;;
        arch)   pkg_install nodejs npm ;;
        fedora)
            fetch_and_run_as_root "https://rpm.nodesource.com/setup_24.x"
            run_sudo dnf install -y nodejs ;;
        *)
            fetch_and_run_as_root "https://deb.nodesource.com/setup_24.x"
            run_sudo apt-get install -y nodejs ;;
    esac
}

# The distro packages auto-start services on their default ports. Benches run
# their own instances, so free the ports and the memory right away. nginx is
# started by `bench setup production`, which a sudoers grant already allows.
disable_system_services() {
    case "$DISTRO" in
        macos|unknown) return 0 ;;
    esac
    for service in mariadb postgresql redis-server redis valkey nginx supervisor; do
        run_sudo systemctl disable --now "$service" 2>/dev/null || true
    done
}

install_system_packages() {
    [ "$DISTRO" = "unknown" ] && return 0
    # Root always runs this (idempotent, and bare containers need it before
    # useradd); the bench user only when a base tool is genuinely missing.
    if ! is_root; then
        base_tools_present && return 0
        if ! command -v sudo >/dev/null 2>&1; then
            echo "sudo is not installed and you are not root, so base packages cannot"
            echo "be installed. Re-run this installer as root first, then as the bench user:"
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

base_tools_present() {
    if [ "$DISTRO" = "macos" ]; then
        tools="git brew python3"
    else
        tools="git curl bash sudo python3"
    fi
    for tool in $tools; do
        command -v "$tool" >/dev/null 2>&1 || return 1
    done
    return 0
}

# ── host provisioning (root only) ─────────────────────────────────────────────
# Each of these exists so the bench user never has to ask for a password later.
# All are idempotent: re-running the installer repairs a host in place.

bench_home() {
    getent passwd "$1" | cut -d: -f6
}

systemd_booted() {
    [ -d /run/systemd/system ] && command -v loginctl >/dev/null 2>&1
}

linger_enabled() {
    [ "$(loginctl show-user "$1" --property=Linger 2>/dev/null)" = "Linger=yes" ]
}

# The group conventionally granted admin rights, so the bench user can
# authenticate sudo interactively if it ever needs to.
admin_group() {
    case "$DISTRO" in
        debian|ubuntu|unknown) echo sudo ;;
        *) echo wheel ;;
    esac
}

create_bench_user() {
    id "$1" >/dev/null 2>&1 && return 0
    echo "Creating user '$1'..."
    useradd -m -s /bin/bash "$1"
    usermod -aG "$(admin_group)" "$1" 2>/dev/null || true
}

# Bench services are `systemctl --user` units, which need this user's systemd
# instance alive with no login session. Enabling lingering also starts it,
# creating the D-Bus socket every systemctl --user call talks to.
enable_linger() {
    systemd_booted || return 0
    linger_enabled "$1" && return 0
    echo "Enabling systemd lingering for '$1'..."
    loginctl enable-linger "$1"
}

# nginx reads each bench's vhost from a file the bench user owns. Dropping the
# one glob that pulls them in makes publishing a vhost an ordinary file write
# rather than a privileged symlink into /etc/nginx.
install_nginx_include() {
    home="$(bench_home "$1")"
    [ -n "$home" ] && [ -d /etc/nginx/conf.d ] || return 0
    echo "Installing the nginx include for '$1'..."
    cat > /etc/nginx/conf.d/00-pilot.conf <<EOF
include $home/pilot/nginx/*.conf;
include $home/pilot/benches/*/config/nginx/include.conf;
EOF
    chmod 644 /etc/nginx/conf.d/00-pilot.conf
    # The stock default site also claims default_server on :80 and nginx
    # rejects the duplicate, so let a bench's vhost win.
    rm -f /etc/nginx/sites-enabled/default
    # Older installs symlinked each bench into conf.d. The glob above loads the
    # same file now, and nginx rejects the duplicate server blocks.
    for link in /etc/nginx/conf.d/*.conf; do
        [ -L "$link" ] || continue
        case "$(readlink "$link")" in
            "$home"/pilot/benches/*) rm -f "$link" ;;
        esac
    done
}

# nginx workers must run as the bench user to read its sites.
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

# logrotate ignores a config it does not own, so the bench user cannot write
# one. This single glob covers every bench and monitor, present and future.
install_logrotate() {
    home="$(bench_home "$1")"
    [ -n "$home" ] && [ -d /etc/logrotate.d ] || return 0
    echo "Installing log rotation for '$1'..."
    cat > /etc/logrotate.d/pilot <<EOF
$home/pilot/logs/*.log $home/pilot/benches/*/logs/*.log {
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

# `command -v` returns the bare name for shell builtins like `test`, but a
# sudoers Cmnd must be a fully-qualified path or visudo rejects the whole file.
# Mirror the runtime's shutil.which by taking only an absolute PATH match.
resolve_binary() {
    resolved="$(command -v "$1" 2>/dev/null || true)"
    case "$resolved" in
        /*) echo "$resolved" ;;
        *) echo "$2" ;;
    esac
}

# Reloading nginx and running certbot need root every time a bench deploys or a
# cert renews. These mirror NginxManager.setup_sudoers and
# LetsEncryptManager.setup_sudoers, which check whether a grant works before
# rewriting it — so the two only have to agree in effect, not in text.
install_sudoers_grants() {
    [ -d /etc/sudoers.d ] || return 0
    command -v visudo >/dev/null 2>&1 || return 0
    nginx_bin="$(resolve_binary nginx /usr/sbin/nginx)"
    certbot_bin="$(resolve_binary certbot /usr/bin/certbot)"
    openssl_bin="$(resolve_binary openssl /usr/bin/openssl)"
    systemctl_bin="$(resolve_binary systemctl /bin/systemctl)"
    mkdir_bin="$(resolve_binary mkdir /bin/mkdir)"
    test_bin="$(resolve_binary test /usr/bin/test)"
    webroot=/var/www/letsencrypt
    live=/etc/letsencrypt/live
    hook="systemctl reload nginx"

    echo "Granting '$1' passwordless sudo for nginx and certbot..."
    write_sudoers_file "$1-pilot-nginx" \
"$1 ALL=(ALL) NOPASSWD: $nginx_bin -t,$nginx_bin -T,$systemctl_bin start nginx,$systemctl_bin stop nginx,$systemctl_bin reload nginx"
    # Domain and email tokens stay wildcarded (sites arrive long after this is
    # written), but each wildcard is anchored between fixed literal text, so no
    # extra flag can be smuggled in before or after the match.
    write_sudoers_file "$1-pilot-certbot" \
"$1 ALL=(ALL) NOPASSWD: $certbot_bin certonly --webroot -w $webroot * --cert-name * --expand --email * --agree-tos --non-interactive --deploy-hook $hook,$certbot_bin certonly --webroot -w $webroot -d * --email * --agree-tos --non-interactive --deploy-hook $hook,$certbot_bin renew --quiet,$mkdir_bin -p $webroot,$test_bin -f $live/*/fullchain.pem -a -f $live/*/privkey.pem,$openssl_bin x509 -noout -ext subjectAltName -in $live/*/fullchain.pem,$openssl_bin x509 -enddate -noout -in $live/*/fullchain.pem"
}

# A malformed file in /etc/sudoers.d breaks sudo for every user, including the
# recovery path, so validate before installing.
write_sudoers_file() {
    staged="$(mktemp)"
    echo "$2" > "$staged"
    if visudo -cf "$staged" >/dev/null 2>&1; then
        install -m 440 "$staged" "/etc/sudoers.d/$1"
    else
        echo "Refusing to install a malformed sudoers grant ($1)." >&2
    fi
    rm -f "$staged"
}

# We never switch users mid-run: prepare the account, then ask the operator to
# come back as that user.
prepare_host() {
    echo "Running as root. Preparing the '$BENCH_USER' user for bench..."
    create_bench_user "$BENCH_USER"
    enable_linger "$BENCH_USER"
    install_nginx_include "$BENCH_USER"
    set_nginx_worker_user "$BENCH_USER"
    install_logrotate "$BENCH_USER"
    install_sudoers_grants "$BENCH_USER"

    echo ""
    echo "========================================================================"
    echo " User '$BENCH_USER' is ready — base tools, database engines and the"
    echo " production stack are installed system-wide, so day-to-day bench"
    echo " commands never need root."
    echo ""
    echo " bench must NOT be installed as root. Switch to '$BENCH_USER' and run"
    echo " the installer again:"
    echo ""
    echo "   su - $BENCH_USER"
    echo "   curl -fsSL $INSTALL_URL | bash"
    echo "========================================================================"
}

# ── bench user install ────────────────────────────────────────────────────────
# Nothing below here needs privileges: prepare_host granted them all already.

# Only root can turn lingering on. Fail now rather than midway through
# `bench init`, where systemctl --user finds no bus to talk to.
require_linger() {
    systemd_booted || return 0
    linger_enabled "$(id -un)" && return 0
    sudo -n loginctl enable-linger "$(id -un)" 2>/dev/null && return 0
    echo "systemd lingering is not enabled for '$(id -un)', and this user cannot" >&2
    echo "enable it without a password. Bench services run as systemctl --user" >&2
    echo "units, which need it." >&2
    echo "" >&2
    echo "Run this as root, then re-run the installer:" >&2
    echo "" >&2
    echo "   loginctl enable-linger $(id -un)" >&2
    exit 1
}

# The release tarball ships the compiled admin frontend and a VERSION file;
# --dev clones the source so contributors build the frontend locally.
fetch_pilot() {
    if [ -n "$DEV_MODE" ]; then
        if [ -d "$PILOT_DIR/.git" ]; then
            echo "Updating pilot (dev)..."
            git -C "$PILOT_DIR" pull
        else
            echo "Cloning pilot ($BRANCH_NAME branch)..."
            git clone -b "$BRANCH_NAME" "$REPO_URL" "$PILOT_DIR"
        fi
        return
    fi

    echo "Fetching the latest pilot release..."
    asset_url=$(curl -fsSL --proto '=https' --tlsv1.2 \
        "https://api.github.com/repos/$GITHUB_SLUG/releases?per_page=1" \
        | grep -o 'https://[^"]*/pilot\.tar\.gz' | head -n1)
    if [ -z "$asset_url" ]; then
        echo "Could not find a pilot.tar.gz release asset." >&2
        echo "Install a development checkout instead with: $INSTALL_URL --dev" >&2
        exit 1
    fi
    tmp="$(mktemp)"
    curl -fsSL --proto '=https' --tlsv1.2 "$asset_url" -o "$tmp"
    # tar only writes archived paths, so an existing benches/ is left intact.
    mkdir -p "$PILOT_DIR"
    tar -xzf "$tmp" -C "$PILOT_DIR"
    rm -f "$tmp"
}

ensure_uv() {
    command -v uv >/dev/null 2>&1 && return 0
    echo "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
}

# Python's zoneinfo needs this on systems without system tzdata.
ensure_tzdata() {
    case "$DISTRO" in
        macos) return 0 ;;
        unknown) command -v apt-get >/dev/null 2>&1 || return 0 ;;
    esac
    pkg_installed tzdata && return 0
    echo "Installing timezone data..."
    pkg_install tzdata
}

# Sets RC_FILE to the rc it touched, for the closing hint.
add_bench_to_path() {
    RC_FILE=""
    case "$SHELL" in
        */fish)       RC_FILE="$HOME/.config/fish/config.fish" ;;
        */zsh)        RC_FILE="$HOME/.zshrc" ;;
        # POSIX login shells read ~/.profile.
        */ash|*/sh|"") RC_FILE="$HOME/.profile" ;;
        *)            RC_FILE="$HOME/.bashrc" ;;
    esac
    mkdir -p "$(dirname "$RC_FILE")"
    if ! grep -qF 'pilot' "$RC_FILE" 2>/dev/null; then
        case "$SHELL" in
            */fish) echo "fish_add_path \$HOME/pilot" >> "$RC_FILE" ;;
            *)      echo "export PATH=\"\$HOME/pilot:\$PATH\"" >> "$RC_FILE" ;;
        esac
        echo "Added bench to PATH in $RC_FILE"
    fi

    export PATH="$PILOT_DIR:$PATH"
    # fish rc syntax is not sh, so never source it back into this shell.
    case "$SHELL" in */fish) RC_FILE=""; return ;; esac
    # Best-effort: shell-specific rc syntax may not parse here, which is fine —
    # the export above already applies and a new terminal reads the rc.
    if [ -f "$RC_FILE" ]; then
        # shellcheck disable=SC1090
        . "$RC_FILE" 2>/dev/null || true
    fi
}

ensure_admin_venv() {
    admin_venv="$PILOT_DIR/.admin-venv"
    [ -f "$admin_venv/bin/python" ] && return 0
    echo "Setting up admin environment..."
    uv venv "$admin_venv" --quiet
    if command -v python3 >/dev/null 2>&1; then
        admin_deps=$(python3 -c "
import tomllib
with open('$PILOT_DIR/pyproject.toml', 'rb') as f:
    project = tomllib.load(f)
print(' '.join(project.get('project', {}).get('optional-dependencies', {}).get('admin', [])))
" 2>/dev/null)
    fi
    if [ -z "$admin_deps" ]; then
        admin_deps="flask>=3.0 psutil>=5.9 pymysql>=1.1 gunicorn>=21.2 pyjwt[crypto]>=2.8"
    fi
    # shellcheck disable=SC2086 # admin_deps is a space-separated list
    uv pip install --python "$admin_venv/bin/python" --quiet $admin_deps
    echo "Admin environment ready."
}

install_for_user() {
    echo "Setting up your environment..."
    require_linger
    fetch_pilot
    chmod +x "$PILOT_DIR/bench"
    ensure_uv
    ensure_tzdata
    add_bench_to_path
    ensure_admin_venv

    echo ""
    echo "bench installed to $PILOT_DIR"
    echo ""
    echo "Quick start:"
    echo "  bench new my-bench"
    echo "  bench start"
    echo ""
    echo "If 'bench' is not found, open a new terminal or run: . ${RC_FILE:-$HOME/.bashrc}"
}

# ── run ───────────────────────────────────────────────────────────────────────
install_system_packages

if is_root; then
    prepare_host
    exit 0
fi

install_for_user
