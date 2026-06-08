# bench-cli

[![Unit Tests](https://github.com/frappe/bench-cli/actions/workflows/unit-tests.yml/badge.svg)](https://github.com/frappe/bench-cli/actions/workflows/unit-tests.yml)

A zero-dependency CLI for managing [Frappe](https://frappeframework.com) environments with Admin UI. Single `bench.toml`. No Docker.

![Apps](docs/screenshots/apps.png)

## Improvements from legacy bench

| | Legacy | bench-cli |
|---|---|---|
| Dependencies | ~20 Python packages | Zero — stdlib only |
| Marketplace | None | App registry `apps.json` |
| Config | None | Single `bench.toml` |
| Folder layout | Wherever you `bench init` | All benches under `bench-cli/benches/` |
| Process manager | Honcho / Supervisor | Built-in Procfile runner |
| Python env | pip + virtualenv | [uv](https://github.com/astral-sh/uv) (auto-installed) |
| Admin UI | None | Built-in — app status, sites, logs, task runner, process memory/CPU |

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

[workers]
default = 2
short = 1
long = 1

[admin]
port = 8002
password = "your-admin-password"   # required — admin refuses to start without this
domain = "admin.example.com"       # optional — serve admin over HTTPS via nginx

[production]
process_manager = "supervisor"   # none | supervisor | systemd
nginx = true
```

Apps and sites are tracked by the filesystem — no need to list them in `bench.toml`.

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

With multiple benches: `bench -b my-bench start`

## Production

```toml
[production]
process_manager = "supervisor"   # none | supervisor | systemd
nginx = true

[nginx]
enabled = true

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

When `admin.domain` is set, `bench setup production` obtains a certificate for that domain and generates an HTTPS nginx proxy block. HTTP redirects to HTTPS automatically.

The admin UI (port 8002 / `admin.domain`) shows Start, Stop, and Restart buttons on the Processes page when running in production mode. The Processes page also displays live CPU and memory usage per process.

## Admin UI

The built-in admin UI runs on port 8002 (configurable via `[admin] port`).

| Page | Features |
|------|----------|
| Dashboard | Bench overview and quick stats |
| Apps | Install/remove apps, edit upstream URL and branch, per-app update status |
| Sites | Create/restore/drop sites, install apps, edit site config, backup schedules |
| Processes | Live process list with CPU %, memory (MB), uptime, and log links; Start/Stop/Restart in production mode |
| Logs | Tail and search log files with live streaming |
| Tasks | Multi-step task view with collapsible output per step; task history |
| Database | MariaDB process list, slow queries, binary log viewer |
| Settings | Modal dialog (sidebar dropdown) — configure ports, workers, process manager, nginx, and Let's Encrypt; check and apply bench-cli updates |

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
