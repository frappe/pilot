# bench-cli

A zero-dependency CLI for managing [Frappe](https://frappeframework.com) environments. Single `bench.toml`. No Docker.

## Improvements from legacy bench

| | Legacy | bench-cli |
|---|---|---|
| Dependencies | ~20 Python packages | Zero — stdlib only |
| Config | Scattered JSON files | Single `bench.toml` |
| Folder layout | Wherever you `bench init` | All benches under `bench-cli/benches/` |
| Process manager | Honcho / Supervisor | Built-in Procfile runner |
| Python env | pip + virtualenv | [uv](https://github.com/astral-sh/uv) (auto-installed) |
| Admin UI | None | Built-in — app status, sites, logs, task runner |
| Site/app tracking | Config files | Filesystem — `apps/` and `sites/` are source of truth |

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
```

Apps and sites are tracked by the filesystem — no need to list them in `bench.toml`.

## Commands

| Command | What it does |
|---------|-------------|
| `bench new <name>` | Scaffold a new bench |
| `bench init` | Install deps, create venv, clone framework, generate Procfile |
| `bench start` | Start all processes (web, workers, Redis, admin UI) |
| `bench stop` | Stop a running bench from another terminal |
| `bench get-app <repo>` | Clone and install an app |
| `bench new-site <name>` | Create a site |
| `bench build` | Rebuild JS/CSS assets |
| `bench update` | git pull + reinstall + migrate all sites |
| `bench update-config` | Regenerate Procfile and config files from bench.toml |
| `bench build-admin` | Rebuild admin frontend assets |
| `bench setup nginx` | Generate and install nginx config |
| `bench setup letsencrypt` | Obtain SSL certificates |
| `bench setup production` | Full production setup (nginx + SSL) |

With multiple benches: `bench -b my-bench start`

## Production

```toml
[nginx]
enabled = true

[letsencrypt]
email = ops@example.com
```

```bash
bench setup production
```

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
