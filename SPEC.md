# bench — Specification

bench is a command-line tool for setting up and managing a Frappe development environment on Ubuntu and macOS. It replaces the original frappe/bench with a simpler, TOML-driven approach that avoids Docker and keeps everything transparent and hackable.

---

## Core ideas

- **One config file** (`bench.toml`) describes the infrastructure: Python version, database, Redis, workers. Apps and sites are managed via commands and discovered from the filesystem.
- **No Docker.** Services (MariaDB, Redis) are installed directly on the host via apt (Ubuntu) or Homebrew (macOS).
- **Zero Python dependencies.** The CLI uses only the Python 3.11+ standard library (`tomllib`, `argparse`, `subprocess`, `threading`, `signal`). No click, no yaml, no psutil.
- **uv for Python environments.** `uv venv` and `uv pip install` manage virtualenvs. uv is auto-installed on first use.
- **Plain Python OOP.** Classes map directly to real-world concepts (Bench, App, Site, Manager). No clever metaprogramming.
- **Web admin** (`bench admin`) provides a read/operate interface over the bench without maintaining its own state.

---

## Quick start

```bash
# Install bench-cli (one time)
git clone https://github.com/frappe/bench-cli
uv tool install ./bench-cli

# Create a bench
bench new my-bench
# Edit benches/my-bench/bench.toml — add your db credentials

bench init -b my-bench # install deps, clone framework app, set up venv
bench new-site site1.localhost   # create your first site
bench start            # start all processes
```

---

## Sub-specifications

| File | What it covers |
|------|---------------|
| [specs/config.md](specs/config.md) | Full `bench.toml` schema with field descriptions and a complete example |
| [specs/architecture.md](specs/architecture.md) | Python package layout, classes, responsibilities, and relationships |
| [specs/commands.md](specs/commands.md) | Step-by-step behaviour of each CLI command |
| [specs/admin.md](specs/admin.md) | Flask admin interface — pages, readers, log streaming |
| [specs/tasks.md](specs/tasks.md) | Task execution model — forked processes, PID/output files, status tracking |
| [specs/production.md](specs/production.md) | DNS multitenancy, Nginx config generation, Let's Encrypt SSL, `bench setup` commands |
| [specs/wireframes.md](specs/wireframes.md) | ASCII wireframes for admin UI — dashboard, sites, database tools, tasks, log viewer |

---

## Guiding constraints

1. **Readable over clever.** A new contributor should be able to understand any class without reading surrounding code.
2. **Fail loudly.** Validate `bench.toml` up-front and print actionable errors before touching the filesystem.
3. **Idempotent where possible.** Running `bench init` twice should not break a working bench.
4. **Ubuntu + macOS.** System package installation targets Ubuntu 22.04 LTS (via apt) and macOS (via Homebrew). Other Debian-based distros are best-effort. Production setup (Nginx, Let's Encrypt) targets Ubuntu/Linux servers; macOS is a development platform only.
5. **Single virtualenv.** All Python apps share one virtualenv inside the bench directory.
6. **Filesystem as source of truth for apps/sites.** `bench.toml` only declares infra config and the initial framework app to clone. After `bench init`, apps and sites are tracked on disk (`apps/`, `sites/`) — not in the config file.
