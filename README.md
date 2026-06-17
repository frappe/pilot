# bench-cli

[![Unit Tests](https://github.com/frappe/bench-cli/actions/workflows/unit-tests.yml/badge.svg)](https://github.com/frappe/bench-cli/actions/workflows/unit-tests.yml)

A zero-dependency CLI for managing [Frappe](https://frappeframework.com) environments with Admin UI. Single `bench.toml`. No Docker.

![Apps](docs/screenshots/apps.png)

## Improvements from legacy bench

| | Legacy | bench-cli |
|---|---|---|
| Dependencies | ~20 Python packages | Zero — stdlib only |
| Marketplace | None | App registry `registry/apps.json` |
| Config | None | Single `bench.toml` |
| Folder layout | Wherever you `bench init` | All benches under `bench-cli/benches/` |
| Process manager | Honcho / Supervisor | Built-in Procfile runner |
| Python env | pip + virtualenv | [uv](https://github.com/astral-sh/uv) (auto-installed) |
| Admin UI | None | Built-in — app status, sites, logs, task runner, process memory/CPU, live settings |
| Storage | Root filesystem only dedicated disk **or** disk image — no spare disk needed with per-dataset quotas, reservations, and snapshots |

## Requirements

**Ubuntu 22.04+** — Python 3.11+, `sudo` access  
**macOS** — Python 3.11+, [Homebrew](https://brew.sh) (dev only)

## Install

```bash
curl -fsSL https://raw.githubusercontent.com/frappe/bench-cli/main/install.sh | bash
```

Clones to `~/bench-cli` and adds `bench` to `PATH`. Or manually:

```bash
git clone https://github.com/frappe/bench-cli ~/bench-cli
echo 'export PATH="$HOME/bench-cli:$PATH"' >> ~/.zshrc && source ~/.zshrc
```

## Passwordless sudo (optional)

`bench init` can write a sudoers drop-in so that subsequent `apt-get`, `nginx`, `systemctl`, and related calls run without a password prompt:

```bash
bench init --sudo-password <your-sudo-password>
```

**The password is never stored.** It is used once to write `/etc/sudoers.d/<user>` via `sudo -S tee`, then immediately discarded. Only the specific commands that bench manages are granted `NOPASSWD` — everything else still requires a password.

The write is idempotent: if the required rules are already in the file, the step is silently skipped.

If the `IS_SUDOERS_SETUP` environment variable is set (e.g. in CI or a managed deployment where the file is pre-provisioned), the password is not requested and the step is skipped entirely.


## Quick start

```bash
bench new my-bench       # creates bench.toml — edit it to set MariaDB password
bench init               # installs deps, creates venv, clones frappe, generates Procfile
bench get-app https://github.com/frappe/erpnext --branch version-16
bench new-site site1.localhost
bench start              # starts web, workers, Redis, and admin UI
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
domain = "admin.example.com"       # optional — serve admin over HTTPS via nginx

[production]
process_manager = "supervisor"   # none | supervisor | systemd
nginx = true
use_companion_manager = false      # run scheduler/workers/socketio inside gunicorn

[gunicorn]
workers = 4
threads = 4                          # threads per worker (used by gthread)
timeout = 120
malloc_arena_max = 2                 # cap glibc malloc arenas to reduce RSS; 0 = unset

[volume]
pool = "bench-pool"
backing = "auto"                 # discover an unused disk, or fall back to a disk image
# backing = "device"             # explicit: dedicated disk
# device = "/dev/sdb"
# backing = "image"              # explicit: preallocated file on the root filesystem
# [volume.image]
# size = "60G"                   # file created at /var/lib/bench-zfs/bench-pool.img
```

Apps and sites are tracked by the filesystem — no need to list them in `bench.toml`. See [docs/volume.md](docs/volume.md) for the full ZFS volume guide.

## Commands

| Command | What it does |
|---------|-------------|
| `bench new <name>` | Scaffold a new bench |
| `bench init` | Install deps, create venv, clone framework, generate Procfile |
| `bench start` | Start all processes (web, workers, Redis, admin UI) |
| `bench stop` | Stop a running bench from another terminal |
| `bench restart` | Restart all processes — supervisor or systemd (production only) |
| `bench get-app <repo>` | Clone and install an app |
| `bench new-site <name>` | Create a site |
| `bench build` | Download pre-built assets (use `--force` to rebuild from source) |
| `bench update` | git pull + reinstall + migrate all sites |
| `bench upgrade` | Pull latest bench-cli and download the admin frontend |
| `bench setup config` | Regenerate Procfile and config files from bench.toml |
| `bench build-admin` | Rebuild admin frontend assets from source |
| `bench setup nginx` | Generate and install nginx config |
| `bench setup letsencrypt` | Obtain SSL certificates |
| `bench setup production` | Full production setup (nginx + SSL + supervisor/systemd) |
| `bench volume status` | Show ZFS pool and dataset usage |
| `bench volume snapshot` | Snapshot both datasets (or `--dataset benches\|mariadb`) |
| `bench volume list-snapshots` | List snapshots per dataset |
| `bench volume destroy-snapshot <tag>` | Destroy a named snapshot |
| `bench volume restore-snapshot <tag>` | Rollback a dataset to a snapshot |

With multiple benches: `bench -b my-bench start`

## Extending the CLI

Commands are **self-registering** — adding one means creating a single file under
`bench_cli/commands/`. No edits to `cli.py` or any central list. Subclass `Command`,
declare its name/help/arguments, and a registry auto-discovers it:

```python
# bench_cli/commands/hello.py
from bench_cli.commands.base import Command


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
process_manager = "supervisor"   # none | supervisor | systemd
nginx = true
use_companion_manager = false      # run scheduler/workers/socketio inside gunicorn

[nginx]
enabled = true

[gunicorn]
workers = 4
threads = 4
timeout = 120
malloc_arena_max = 2

[letsencrypt]
email = "ops@example.com"

[admin]
port = 8002
password = "your-admin-password"
domain = "admin.example.com"   # optional — serve admin UI over HTTPS
```

```bash
bench setup production         # process manager (supervisor or systemd) + nginx + SSL
bench restart                  # restart all bench processes (works with both managers)
```

**Process managers:**
- **Supervisor** — runs a bench-owned `supervisord` instance, no root needed.
- **Systemd** — uses `systemctl --user` units; requires `loginctl enable-linger` once.
- **None** — development mode; use `bench start` / Procfile runner.

**Companion manager:**
Set `production.use_companion_manager = true` to run the scheduler, RQ workers, and socket.io as Gunicorn companion processes. This keeps them under the same preloaded Gunicorn master to share memory copy-on-write. Requires the Frappe Gunicorn fork with companion support.

When `admin.domain` is set, `bench setup production` obtains a certificate for that domain and generates an HTTPS nginx proxy block. HTTP redirects to HTTPS automatically.

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
| Settings | Tabbed modal — Bench ports, MariaDB (read-only), Redis ports, Workers, Nginx, Let's Encrypt, Production process manager, ZFS Volume (Linux); saves to `bench.toml` and restarts affected processes automatically |
| Updates | Check for bench-cli updates and apply in one click |

All forms validate input before submission — site names are checked for valid hostname format, repository URLs for valid git URL format, branch names for legal characters, cron expressions for valid 5-field syntax, and port numbers for the 1–65535 range.

## Directory layout

```
bench-cli/
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
pytest tests/ --ignore=tests/integration --cov=bench_cli --cov-report=term-missing
```

Unit tests run against mocked filesystems — no MariaDB, Redis, or network required.

Integration tests (in `tests/integration/`) run the full `bench init` → `bench new-site` lifecycle against real services and are triggered by CI on push to `main`.
