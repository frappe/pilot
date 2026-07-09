# Pilot

[![Unit Tests](https://github.com/frappe/pilot/actions/workflows/unit-tests.yml/badge.svg)](https://github.com/frappe/pilot/actions/workflows/unit-tests.yml)

A zero-dependency CLI for managing [Frappe](https://frappeframework.com) environments with Admin UI. Single `bench.toml`. No Docker.

![Apps](docs/screenshots/apps.png)

## Improvements from legacy bench

| | Legacy | Pilot |
|---|---|---|
| Dependencies | ~20 Python packages | Zero — stdlib only |
| Marketplace | None | App registry `registry/apps.json` |
| Config | None | Single `bench.toml` |
| Folder layout | Wherever you `bench init` | All benches under `pilot/benches/` |
| Process manager | Honcho / Supervisor | Built-in Procfile runner |
| Python env | pip + virtualenv | [uv](https://github.com/astral-sh/uv) (auto-installed) |
| Admin UI | None | Built-in — app status, sites, logs, task runner, process memory/CPU, live settings |
| Storage | Root filesystem only dedicated disk **or** disk image — no spare disk needed with per-dataset snapshots |

## Requirements

**Ubuntu 22.04+** — Python 3.11+, a user with `sudo` access  
**Alpine 3.20+** — apk + OpenRC; `install.sh` bootstraps everything. Production runs under OpenRC (`process_manager = "openrc"`) instead of systemd  
**macOS** — Python 3.11+, [Homebrew](https://brew.sh) (dev only — no `sudo` setup)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/frappe/pilot/main/install.sh | bash
```

On bare Alpine (no curl/bash preinstalled) bootstrap with busybox `wget` + `sh`
instead — the installer apk-installs git, curl, bash, sudo and the build deps itself:

```sh
wget -qO- https://raw.githubusercontent.com/frappe/pilot/main/install.sh | sh
```

This single command:

- Clones pilot to `~/pilot` and adds `bench` to your `PATH`
- Installs [`uv`](https://github.com/astral-sh/uv) and Node.js if missing
- Creates the isolated admin environment (`.admin-venv`) used by the setup wizard and admin UI
- Configures **passwordless sudo** for your user (see below)

### Running as root (fresh VPS)

bench is never meant to run as root. If you launch the installer as **root** — common on a
freshly provisioned VPS where `root` is the only account — it will:

1. Create (or reuse) a non-root user — `frappe` by default, or whatever you choose
2. Grant that user passwordless sudo via `/etc/sudoers.d/<user>`
3. Re-run the rest of the install **as that user** automatically

No password is set for a freshly created user — login stays SSH-key based, and passwordless
sudo covers everything bench needs. This also means a brand-new VPS user with no password set
works out of the box.

### Non-interactive install

Pass flags after `--`, or set the matching environment variables, to skip all prompts:

| Flag | Env var | Default | Purpose |
|------|---------|---------|---------|
| `--user <name>` | `BENCH_USER` | `frappe` | Non-root user to create/use when run as root |
| `-y`, `--yes` | `BENCH_YES=1` | off | Never prompt; accept defaults |

```bash
# Unattended, as root: create/use user "frappe" and finish silently
curl -fsSL https://raw.githubusercontent.com/frappe/pilot/main/install.sh | bash -s -- --user frappe -y
```

### Manual install

```bash
git clone https://github.com/frappe/pilot ~/pilot
echo 'export PATH="$HOME/pilot:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

If you install manually, make sure your user has **passwordless sudo** — bench needs it (see
below). The one-line installer sets this up for you.

### Passwordless sudo

`bench init` and the admin setup wizard install system packages and manage services through
many `sudo` calls, and the wizard runs them with **no terminal** (it can't answer a password
prompt). bench therefore requires passwordless sudo for your user. The installer configures it;
if it's missing, `bench init` stops immediately with instructions instead of hanging. To set it
up by hand:

```bash
echo "$(whoami) ALL=(ALL) NOPASSWD: ALL" | sudo tee /etc/sudoers.d/$(whoami)
sudo chmod 0440 /etc/sudoers.d/$(whoami)
```

## Set up your first bench

After installing, you have two ways to configure and initialize a bench.

### Guided setup (recommended)

```bash
bench new my-bench       # scaffold a bench
bench start              # not yet initialized → launches the setup wizard
```

`bench start` detects that the bench isn't initialized and opens a browser-based wizard at
`http://localhost:8002`. The wizard walks you through three steps:

1. **Admin password** — password for the bench admin UI
2. **Database** — choose between a dedicated MariaDB instance (default, recommended) or the shared system MariaDB; set the MariaDB root user (default `root`) and password
3. **Customize** — Frappe branch/repo; optionally enable ZFS volumes (dedicated DB only)

It then runs the full initialization with a live progress view.

### Manual setup

```bash
bench new my-bench                      # creates bench.toml
$EDITOR benches/my-bench/bench.toml     # set admin password, MariaDB root password (and admin_user if not root)
bench init -b my-bench                  # installs deps, creates venv, clones frappe, generates Procfile
bench get-app https://github.com/frappe/erpnext --branch version-16
bench new-site site1.localhost
bench start                             # starts web, workers, Redis, and admin UI
```

- App: `http://site1.localhost:8000`
- Admin UI: `http://localhost:8002`

## bench.toml

```toml
[bench]
name = "my-bench"
python = "3.14"

[[apps]]
name = "frappe"
repo = "https://github.com/frappe/frappe"
branch = "version-16"

[mariadb]
host = "localhost"
port = 3306
root_password = "your_root_password"

[redis]
port = 13000

[[workers]]
queues = ["default", "short", "long"]
count = 1

[admin]
port = 8002
password = "your-admin-password"   # required — admin refuses to start without this
jwt_secret = "..."                 # auto-generated — signs admin session tokens (don't set by hand)
domain = "admin.example.com"       # optional — serve admin behind this domain via nginx
tls = false                        # server-wide HTTPS opt-in (Let's Encrypt); false = plain HTTP

[production]
enabled = true                     # set by `bench setup production`
process_manager = "supervisor"     # systemd | supervisor | openrc
use_companion_manager = false      # run scheduler/workers/socketio inside gunicorn

[gunicorn]
workers = 4
threads = 4                          # threads per worker (used by gthread)
timeout = 120
malloc_arena_max = 2                 # cap glibc malloc arenas to reduce RSS; 0 = unset
max_requests = 0                     # recycle the web worker after N requests to release heap; 0 = disabled
max_requests_jitter = 0              # random +/- spread on max_requests so workers don't all recycle at once

[volume]
pool = "bench-pool"              # shared pool, reused if it exists; one dataset per bench
backing = "auto"                 # discover an unused disk, or fall back to a disk image
# backing = "device"             # explicit: dedicated disk
# device = "/dev/sdb"
# backing = "image"              # explicit: preallocated file on the root filesystem
# [volume.image]
# size = "60G"                   # file created at /var/lib/bench-zfs/bench-pool.img
```

Each bench lives on a single dataset (`<pool>/<bench>`) holding both its files and its MariaDB data via bind mounts, so snapshots/rollbacks are atomic across both. Apps and sites are tracked by the filesystem — no need to list them in `bench.toml`. See [docs/volume.md](docs/volume.md) for the full ZFS volume guide.

## Commands

| Command | What it does |
|---------|-------------|
| `bench new <name>` | Scaffold a new bench |
| `bench ls` | List all benches with status, production state, and admin URL |
| `bench init -b <name>` | Install deps, create venv, clone framework, generate Procfile (needs `-b <name>` or run inside the bench dir) |
| `bench start` | Start all processes (web, workers, Redis, admin UI) |
| `bench stop` | Stop a running bench from another terminal |
| `bench restart` | Restart all processes — supervisor, systemd, or OpenRC (production only) |
| `bench diagnostics` | Run health checks for resources, processes, Redis, database reachability, and workers |
| `bench get-app <repo>` | Clone and install an app |
| `bench new-site <name>` | Create a site |
| `bench rename-site <old> <new>` | Rename a site (checks the hostname is free across all benches) |
| `bench build` | Download pre-built assets (use `--force` to rebuild from source) |
| `bench update [--apps ..]` | git pull → reinstall deps → rebuild assets → migrate all sites; fails fast on the first error; on ZFS benches takes a snapshot first and auto-rolls back on failure |
| `bench upgrade` | Pull latest pilot and download the admin frontend |
| `bench setup config` | Regenerate Procfile and config files from bench.toml |
| `bench build-admin` | Rebuild admin frontend assets from source |
| `bench generate-admin-session` | Issue a 5-minute, single-use admin sign-in link (`--full-path` for the URL) |
| `bench issue-site-token <name>` | Issue a scoped JWT for site-to-bench API calls (use as `Authorization: Bearer`) |
| `bench set-admin-password` | Set the admin UI password (prompts securely if `--password` is omitted) |
| `bench setup nginx` | Generate and install nginx config |
| `bench setup letsencrypt` | Obtain SSL certificates |
| `bench setup production` | Full production setup — `--process-manager`, `--admin-domain`, `--tls` |
| `bench remove production` | Tear down production, return to dev (keeps certs/logs/domain) |
| `bench volume status` | Show ZFS pool and dataset usage |
| `bench volume snapshot` | Snapshot the bench (files + database) |
| `bench volume list-snapshots` | List snapshots |
| `bench volume destroy-snapshot <tag>` | Destroy a named snapshot |
| `bench volume restore-snapshot <tag>` | Roll the bench back to a snapshot |

With multiple benches: `bench -b my-bench start`

## Developing Frappe Apps with Pilot

Use a source checkout when working on Pilot itself:

```bash
git clone https://github.com/frappe/pilot ~/pilot
cd ~/pilot
export PATH="$PWD:$PATH"
```

The `bench` script at the repository root runs the code from this checkout, so
changes to Python files are picked up the next time you run a command.

Create a local bench and start it in development mode:

```bash
bench new dev-bench
bench start              # first run opens the setup wizard
```

For a tighter development loop, enable the dev watchers in
`benches/dev-bench/bench.toml`:

```toml
[bench]
watch_apps_js = true     # run Frappe's JS asset watcher with bench start
reload_python = true     # reload the dev web (frappe) process when Python files change
watch_admin_js = true    # run the admin UI Vite dev server (if you are building the Pilot Admin UI)
```

Then run the bench normally:

```bash
bench -b dev-bench start
```

In this mode `bench start` regenerates the Procfile/common site config first,
starts Redis, web, workers, socket.io, the admin backend, and any enabled watch
processes in the foreground. Use `bench -b dev-bench stop` from another terminal
to stop a running bench.

## Extending the CLI

Commands are **self-registering** — adding one means creating a single file under
`pilot/commands/`. No edits to `cli.py` or any central list. Subclass `Command`,
declare its name/help/arguments, and a registry auto-discovers it:

```python
# pilot/commands/hello.py
from pilot.commands.base import Command


class HelloCommand(Command):
    name = "hello"
    help = "Print a greeting."
    requires_bench = False          # omit to receive the active Bench

    def run(self) -> None:
        print("hello")
```

That's the whole change — `bench hello` now works. Commands that take arguments add an
`add_arguments(parser)` classmethod and a `from_args(args, bench)` factory; set
`group = "setup"` (or `"volume"`) to nest under a subcommand group. See
[docs/architecture.md](docs/architecture.md#cli-entry-point-and-command-registry).

## Production

```toml
[production]
enabled = true                   # set by `bench setup production`
process_manager = "supervisor"   # systemd | supervisor | openrc
use_companion_manager = false      # run scheduler/workers/socketio inside gunicorn

[gunicorn]
workers = 4
threads = 4
timeout = 120
malloc_arena_max = 2
max_requests = 0

[letsencrypt]
email = "ops@example.com"

[admin]
port = 8002
password = "your-admin-password"
jwt_secret = "..."             # auto-generated — signs admin session tokens (don't set by hand)
domain = "admin.example.com"   # required in production — admin is served behind this domain
tls = true                     # server-wide HTTPS via Let's Encrypt (omit/false = plain HTTP)
```

```bash
bench setup production --tls   # process manager + nginx + SSL; flags: --process-manager, --admin-domain, --tls
bench restart                  # restart all bench processes (works with both managers)
bench remove production        # tear down production, back to dev (keeps certs/logs/domain)
```

**Process managers:**
- **Supervisor** — runs a bench-owned `supervisord` instance, no root needed.
- **Systemd** — uses `systemctl --user` units; requires `loginctl enable-linger` once.
- **OpenRC** — the Alpine counterpart of systemd: one `supervise-daemon` init script per process under `/etc/init.d/`. Selected automatically on Alpine.
- **None** — development mode; use `bench start` / Procfile runner.

**Companion manager:**
Set `production.use_companion_manager = true` to run the scheduler, RQ workers, and socket.io as Gunicorn companion processes. This keeps them under the same preloaded Gunicorn master to share memory copy-on-write. Requires the Frappe Gunicorn fork with companion support.

HTTPS is opt-in: with `admin.tls = true`, `bench setup production` (or the Settings → HTTPS toggle) obtains Let's Encrypt certificates for the admin domain and all SSL-enabled sites and redirects HTTP to HTTPS. With `admin.tls = false` the bench is served over plain HTTP (e.g. a central proxy terminates TLS upstream), and per-site SSL is hidden in the UI.

The admin UI (port 8002 / `admin.domain`) shows Start, Stop, and Restart buttons on the Processes page when running in production mode. The Processes page also displays live CPU and memory usage per process.

## Admin UI

The built-in admin UI runs on port 8002 (configurable via `[admin] port`).

| Page | Features |
|------|----------|
| Dashboard | Bench overview and quick stats |
| Apps | Install/remove apps, edit upstream URL and branch, per-app update status |
| Marketplace | App registry — filter by 6 categories, search, install with branch selection |
| Sites | Create/restore/drop sites, install apps, edit site config, backup schedules |
| Processes | Live process list with CPU %, memory (MB), uptime, and log links; Start/Stop/Restart in production mode |
| Logs | Tail and search log files with live streaming |
| Tasks | Multi-step task view with collapsible output per step; task history |
| Database | MariaDB process list, slow queries, binary log viewer |
| Settings | Tabbed — Bench ports, MariaDB (read-only), Redis ports, Workers, Nginx, HTTPS toggle (`admin.tls` + Let's Encrypt), Production process manager, ZFS Volume (Linux); saves to `bench.toml` and restarts affected processes automatically |
| Updates | Check for pilot updates and apply in one click |

All forms validate input before submission — site names are checked for valid hostname format, repository URLs for valid git URL format, branch names for legal characters, cron expressions for valid 5-field syntax, and port numbers for the 1–65535 range.

## Directory layout

```
pilot/
└── benches/
    └── my-bench/
        ├── bench.toml              # infra config (python, db, redis, workers)
        ├── apps/                   # cloned app source
        ├── sites/
        │   ├── apps.txt
        │   ├── common_site_config.json
        │   └── site1.localhost/
        ├── env/                    # Python virtualenv (managed by uv)
        ├── logs/                   # per-process log files
        ├── pids/                   # bench.pid + per-process PID files
        └── config/                 # Procfile, Redis configs, Nginx configs
```

## Contributing an app to the marketplace

Add an entry to `registry/apps.json` and open a PR. Every PR that touches this file runs an automated Semgrep security scan against the app's source code. The PR cannot be merged until the scan passes.

### Entry format

```json
{
  "name": "my_app",
  "title": "My App",
  "description": "One-sentence description of what the app does.",
  "repo": "https://github.com/your-org/my-app",
  "branch": "version-16",
  "branches": ["version-15", "version-16"],
  "logo_url": "https://example.com/logo.png",
  "category": "Applications",
  "stars": 0
}
```

| Field | Required | Notes |
|---|---|---|
| `name` | Yes | Unique snake_case identifier |
| `title` | Yes | Human-readable display name |
| `description` | Yes | Short description shown in the marketplace UI |
| `repo` | Yes | Public GitHub repo URL |
| `branch` | Yes | Default branch installed when a user picks this app |
| `branches` | Yes | All available version branches |
| `logo_url` | No | Direct URL to a square PNG/SVG logo; `null` if none |
| `category` | Yes | One of: `Applications`, `Compliance`, `Developer Tools`, `Extensions`, `Integrations`, `Utilities` |
| `stars` | No | Leave as `0`; the registry sync job updates this automatically |

### What the security scan checks

When your PR is opened, CI clones your repo at the specified `branch` and runs Semgrep against it. **Blocking** findings (which fail the PR) include:

- **Code injection** — `eval()`, `exec()`, `compile()`, `safe_eval()`
- **Template injection** — `render_template` with dynamic input, direct `jinja2.Environment` / `Template` construction
- **SQL injection** — f-strings or `.format()` inside `frappe.db.sql()`
- **Command execution** — `subprocess` with `shell=True`, `os.system`, `execute_in_shell`
- **Authorization bypass** — `ignore_permissions=True` in whitelist methods, `frappe.set_user`
- **Multitenancy violations** — module-level globals, `redis.set`/`redis.get` without scoping

Non-blocking findings (WARNING severity) are reported but do not prevent merge — a Frappe reviewer will note them in the PR.

## Testing

```bash
# Install test dependencies
pip install -e ".[test]"

# Run unit tests
pytest tests/ --ignore=tests/integration

# Run with coverage
pytest tests/ --ignore=tests/integration --cov=pilot --cov-report=term-missing
```

Unit tests run against mocked filesystems — no MariaDB, Redis, or network required.

Integration tests (in `tests/integration/`) run the full `bench init` → `bench new-site` lifecycle against real services and are triggered by CI on push to `main`.
