# bench.toml — Configuration Specification

`bench.toml` is the single source of truth for a bench's infrastructure configuration. It lives at `benches/<name>/bench.toml`.

Apps and sites are **not** tracked in `bench.toml` after `bench init` — they are discovered from the filesystem (`apps/` and `sites/` directories). The `[[apps]]` section exists only to declare the framework app to clone on first init.

---

## Full schema

```toml
# ── Bench identity ────────────────────────────────────────────────────────────
[bench]
name = "my-bench"       # used in process names and log prefixes
python = "3.14"         # Python version to use for the virtualenv
db_type = "mariadb"     # database engine for this bench's sites: "mariadb" or "postgres"
watch_apps_js = false   # set true to start the frappe JS asset watcher with bench start in dev mode
reload_python = false   # set true to autoreload the dev web process on Python changes
watch_admin_js = false  # set true to run the admin UI Vite dev server with bench start

# ── Framework app (cloned during bench init) ──────────────────────────────────
[[apps]]
name = "frappe"
repo = "https://github.com/frappe/frappe"
branch = "version-16"

# ── MariaDB ───────────────────────────────────────────────────────────────────
[mariadb]
host = "localhost"
port = 3306
root_password = "root"   # must match the running MariaDB; set as the root password on a fresh dedicated instance
admin_user = "root"      # MariaDB user bench connects as for admin ops; change if your root account has a different name
# version = "11.8"       # optional — defaults to MariaDB 11.8 LTS (vendor repo on Linux)
# instance = "my-bench"  # set by `bench new` on Linux — gives this bench its own mariadb@<instance>; clear for shared
# socket_path = "/run/mysqld/mysqld-my-bench.sock"  # per-instance socket (auto-derived from instance name)
# data_dir = "/var/lib/mysql-my-bench"              # per-instance datadir (auto-derived)

# ── PostgreSQL (used when bench.db_type = "postgres"; installed by init) ──────
# `bench new` generates the password. On systemd Linux a postgres bench gets its
# own cluster; on Alpine/macOS it shares the system server (port 5432).
[postgres]
host = "localhost"
port = 5432
root_password = ""       # superuser password bench sets and new-site connects with
admin_user = "postgres"  # PostgreSQL superuser bench provisions and connects as
# version = "16"         # major version for the Homebrew formula on macOS
# instance = "my-bench"  # set by `bench new` on systemd Linux — own cluster on its own port; clear for shared

# ── Redis ─────────────────────────────────────────────────────────────────────
[redis]
port = 13000            # single Redis instance for all services (simplest)
# version = "7"         # optional — pin to a specific Redis major version
# or use separate instances:
# cache_port = 13000
# queue_port = 11000
# socketio_port = 12000

# ── Workers ───────────────────────────────────────────────────────────────────
# Each [[workers]] group spawns `count` workers listening to `queues`.
[[workers]]
queues = ["default", "short", "long"]   # one worker handling all three queues
count = 1

# ── Production (set by `bench setup production`) ──────────────────────────────
# [production]
# enabled = false        # true once deployed; cleared by `bench remove production`
# process_manager = ""   # systemd | supervisor | openrc

# ── Nginx (production only) ───────────────────────────────────────────────────
[nginx]
http_port = 80
https_port = 443
config_dir = "/etc/nginx/conf.d"
worker_processes = "auto"
client_max_body_size = "50m"

# ── Gunicorn (production only) ───────────────────────────────────────────────
[gunicorn]
workers = 4             # number of Gunicorn worker processes
threads = 4             # threads per worker (used by gthread worker class)
timeout = 120
worker_class = "sync"
malloc_arena_max = 2    # cap glibc malloc arenas to reduce RSS; 0 = leave unset
max_requests = 0        # recycle the web worker after N requests to release heap; 0 = disabled
max_requests_jitter = 0 # random +/- spread on max_requests

# ── Let's Encrypt (production only) ──────────────────────────────────────────
[letsencrypt]
email = "admin@example.com"  # required if any site has ssl = true
webroot_path = "/var/www/letsencrypt"

# ── Admin UI ──────────────────────────────────────────────────────────────────
[admin]
port = 8002             # port the admin UI listens on
password = "secret"     # required — admin refuses to start without this
domain = ""             # optional — serve admin over HTTPS via nginx (production)
allow_bench_management = true  # set false to hide the bench switcher/manager UI and disable /api/benches/*
```

---

## Field reference

### `[bench]`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `name` | string | yes | — | Human-readable bench name. Used in process labels and log file names. Must match `^[a-zA-Z][a-zA-Z0-9_-]*$`. |
| `python` | string | yes | — | Python version string (e.g. `"3.14"`). Must be available on the system or installable via `deadsnakes/ppa`. |
| `db_type` | string | no | `mariadb` | Database engine for **all** sites on this bench: `"mariadb"` or `"postgres"`. Chosen at `bench new` (`--database`), in the setup wizard, or the admin New Bench dialog. `bench init` installs and provisions only this engine. |
| `watch_apps_js` | bool | no | `false` | In development mode, add a `watch` process to `bench start` that runs Frappe's JS asset watcher. |
| `reload_python` | bool | no | `false` | In development mode, allow the web process to autoreload on Python changes by omitting `frappe serve --noreload`. |
| `watch_admin_js` | bool | no | `false` | In development mode, run the admin UI Vite dev server with hot reload instead of serving rebuilt static admin assets. |

### `[[apps]]`

Declares the framework app (frappe) to clone during `bench init`. After init, additional apps are added via `bench get-app` and are tracked on the filesystem, not in this file.

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `name` | string | yes | Directory name under `apps/` and the Python package name used for `uv pip install -e`. |
| `repo` | string | yes | Git remote URL (HTTPS or SSH). |
| `branch` | string | yes | The git branch to checkout. |

**Constraints:**
- `name` values must be unique.
- The first (and typically only) app listed is treated as the **framework app** (frappe). It must expose a `bench` CLI entry point for site management commands.

### `[mariadb]`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `host` | string | no | `localhost` | MariaDB server host. |
| `port` | int | no | `3306` | MariaDB server port. |
| `root_password` | string | yes | — | Root password used to create site databases and users during `bench init`. For a dedicated instance, `bench init` sets this password via `secure_installation`; for a shared instance the password must already match the running server. |
| `admin_user` | string | no | `root` | MariaDB user bench connects as for admin operations (creating databases, users, running secure_installation). Defaults to `root`; change this if your MariaDB root account uses a different username. |
| `version` | string | no | `11.8` | MariaDB version to install (e.g. `"11.8"`, `"11.4"`). On Linux, bench adds MariaDB's official APT repository pinned to this version and installs `mariadb-server` from it; on macOS it selects the `mariadb@<version>` Homebrew formula. Omit to install the default **11.8 LTS** series. |
| `socket_path` | string | no | — | Unix socket to connect through. For a dedicated instance this is the per-instance socket (e.g. `/run/mysqld/mysqld-<instance>.sock`). |
| `instance` | string | no | — | **Dedicated vs shared MariaDB.** When empty, the bench connects to the shared system MariaDB (`mariadb.service`, port 3306). When set, the bench gets its own MariaDB instance with an isolated datadir, socket, and port — a `mariadb@<instance>` systemd unit on most Linux, or a generated `mariadb-<instance>` OpenRC service on Alpine. `bench new` sets this to the bench name by default on Linux (both systemd and Alpine); the setup wizard lets you clear it to use the shared server instead. macOS always uses the shared server. |
| `data_dir` | string | no | `/var/lib/mysql-<instance>` | Datadir for the dedicated instance — a **sibling** of `/var/lib/mysql`, never nested inside it. Must be an absolute path. Ignored in shared mode. |

> **Dedicated vs shared.** On Linux, `bench new` defaults to a dedicated instance, giving the bench its own isolated MariaDB server. Choose shared (clear `instance` in the setup wizard) to connect to the pre-existing system MariaDB — useful when you already manage MariaDB separately. See [Per-bench MariaDB instances](architecture.md#per-bench-mariadb-instances) for the mechanics.

### `[postgres]`

Used when the bench's engine is PostgreSQL (`[bench] db_type = "postgres"`). `bench init` installs PostgreSQL (apt/Homebrew/apk) and provisions it: it ensures the `admin_user` superuser exists and sets its password to `root_password`. The engine is chosen per bench, not per site — every site on a PostgreSQL bench uses PostgreSQL (and a MariaDB bench never installs PostgreSQL). `bench new` generates `root_password` automatically.

**Dedicated vs shared.** On systemd Linux, `bench new` defaults to a dedicated cluster (`pg_createcluster`) with its own `instance` and `port`; clear `instance` (setup wizard) for the shared system server on 5432. Alpine and macOS always use the shared server.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `host` | string | no | `localhost` | PostgreSQL server host. |
| `port` | int | no | `5432` | Server port. Shared benches use 5432; a dedicated cluster gets its own port. |
| `root_password` | string | no | — | Superuser password bench sets during provisioning and new-site connects with. `bench new` generates one; if empty, provisioning skips superuser setup and you must set it before creating Postgres sites. |
| `admin_user` | string | no | `postgres` | PostgreSQL superuser bench provisions and connects as. |
| `version` | string | no | `16` | Major version: selects the Homebrew formula (`postgresql@<version>`) on macOS and the cluster version for a dedicated Linux cluster. Defaults to the installed server's version on Linux. |
| `instance` | string | no | — | Set on systemd Linux for a dedicated cluster; empty means the shared system server. |

### `[redis]`

**Single-instance mode** (recommended for most benches): specify one `port` and a single Redis server handles all three services using separate database numbers (`/0` cache, `/1` queue, `/2` socketio).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `port` | int | no | — | Run a single Redis instance on this port for all services. When set, `cache_port`/`queue_port`/`socketio_port` are ignored. |
| `cache_port` | int | no | `13000` | Port for the Redis cache instance (multi-instance mode). |
| `queue_port` | int | no | `11000` | Port for the Redis queue instance (multi-instance mode). |
| `socketio_port` | int | no | `12000` | Port for the Redis socketio instance (multi-instance mode). |
| `version` | string | no | — | Redis version to install (e.g. `"7"`, `"7.0"`). On macOS, selects the `redis@<version>` Homebrew formula. On Ubuntu, apt has no versioned redis package names — use the official Redis apt repository for version pinning, then omit this field. |

In single-instance mode, one `redis` process appears in the Procfile and one `redis.conf` is written to `config/`. In multi-instance mode, three separate processes (`redis_cache`, `redis_queue`, `redis_socketio`) and three config files are generated. All ports must be in the range 1024–65535.

### `[[workers]]`

An array of worker groups. Each group spawns `count` worker processes that
listen to the queues in `queues`. Omitting the table entirely defaults to a
single worker handling all three standard queues (`default`, `short`, `long`).

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `queues` | list of strings | yes | — | Queues this group's workers listen to (e.g. `["default", "short", "long"]`). |
| `count` | int | yes | — | Number of worker processes to spawn for this group (≥ 1). |

```toml
# One worker per queue:
[[workers]]
queues = ["default"]
count = 2

[[workers]]
queues = ["short"]
count = 1

[[workers]]
queues = ["long"]
count = 1
```

### `[production]`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | bool | no | `false` | `true` once the bench is deployed to production. Set by `bench setup production` and cleared by `bench remove production`; gates `bench restart`, nginx setup, and production process management. |
| `process_manager` | string | no | `""` | Production process manager: `systemd`, `supervisor`, or `openrc` (Alpine). Empty on an undeployed bench. |
| `use_companion_manager` | bool | no | `false` | Run scheduler, RQ workers, and socket.io as Gunicorn companion processes under a single preloaded master. Requires the Frappe Gunicorn fork with companion support. |

### `[nginx]` _(production only)_

Omit this section entirely for development benches. The section is only read by `bench setup nginx` and `bench setup production` (which run when `production.enabled = true`). nginx is mandatory for production — there is no separate enable flag.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `http_port` | int | no | `80` | Port Nginx listens on for plain HTTP. |
| `https_port` | int | no | `443` | Port Nginx listens on for HTTPS. |
| `config_dir` | string | no | `/etc/nginx/conf.d` | System directory where the bench include-pointer file is symlinked. Requires sudo. |
| `worker_processes` | string or int | no | `auto` | Passed to the Nginx `worker_processes` directive. |
| `client_max_body_size` | string | no | `50m` | Maximum upload size. Increase for large file imports. |

### `[gunicorn]` _(production only)_

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `workers` | int | no | `4` | Number of Gunicorn worker processes. |
| `threads` | int | no | `4` | Threads per worker. Used by the `gthread` worker class. |
| `timeout` | int | no | `120` | Request timeout in seconds. |
| `worker_class` | string | no | `sync` | Gunicorn worker class. |
| `malloc_arena_max` | int | no | `2` (new benches); `0` if absent | Caps glibc malloc arenas (`MALLOC_ARENA_MAX`) for the web/companion/worker Python processes to keep idle RSS down on these multi-threaded processes. `0` leaves the system default unset. |
| `max_requests` | int | no | `0` | Recycle each web worker after this many requests, re-forking it from the preloaded master to release the heap it accreted under load. `0` disables it (safe for production); set e.g. `2000` on demo/overcommit benches to bound RSS. |
| `max_requests_jitter` | int | no | `0` | Random ± spread on `max_requests` so workers don't all recycle at once. |

### `[letsencrypt]` _(production only)_

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `email` | string | yes (if any site has `ssl = true`) | — | Contact email for ACME account registration. |
| `webroot_path` | string | no | `/var/www/letsencrypt` | Directory certbot writes challenge files to. Nginx serves this path at `/.well-known/acme-challenge/`. |

### `[admin]`

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | bool | no | `false` | Whether the admin API serves requests. Production deploys enable it automatically so the admin is reachable behind its domain. |
| `port` | int | no | `8002` | Port the admin process listens on. |
| `password` | string | yes | — | Password for the admin UI. The process refuses all requests with HTTP 503 if this is empty. |
| `domain` | string | no | `""` | Hostname to serve the admin UI in production (e.g. `admin.example.com`). When set, `bench setup production` generates an nginx proxy block (and obtains a certificate if `tls = true`). |
| `tls` | bool | no | `false` | Server-wide HTTPS opt-in. When `true`, the admin and SSL-enabled sites are served over HTTPS with Let's Encrypt; HTTP is redirected. When `false`, everything is served over plain HTTP (a central proxy may terminate TLS upstream). |
| `allow_bench_management` | bool | no | `true` | When `false`, hides the multi-bench UI (bench switcher and New Bench dialog) and returns 403 for every `/api/benches/*` route. The CLI (`bench new`, `bench drop`, …) is unaffected. `bench.toml`-only, no UI toggle; intended for single-tenant/cloud deploys. |

---

## Validation rules

bench validates `bench.toml` before executing any command. Violations produce a clear error message that names the offending field.

1. Required fields (`bench.name`, `bench.python`, `mariadb.root_password`) must be present.
2. `bench.name` must match `^[a-zA-Z][a-zA-Z0-9_-]*$`.
3. All `apps[].name` values must be unique.
4. All Redis ports must be integers in the range 1024–65535. In multi-instance mode (`cache_port`/`queue_port`/`socketio_port`), each port must be distinct.
5. Worker counts must be positive integers.
6. `letsencrypt.email` must match a basic email pattern (`^[^@]+@[^@]+\.[^@]+$`) when present.
7. `nginx.http_port` and `nginx.https_port` must be distinct.
8. `gunicorn.workers`, `gunicorn.threads`, and `gunicorn.timeout` must be positive integers; `gunicorn.worker_class` must be a non-empty string; `gunicorn.malloc_arena_max`, `gunicorn.max_requests`, and `gunicorn.max_requests_jitter` must be non-negative integers.
9. `mariadb.version` and `redis.version`, when present, must match `^\d+(\.\d+)*$` (e.g. `"10.6"`, `"7"`, `"7.0"`).
10. `mariadb.instance`, when present, must match `^[a-zA-Z][a-zA-Z0-9_-]*$`; `mariadb.data_dir`, when present, must be an absolute path.

---

## Minimal example

```toml
[bench]
name = "dev"
python = "3.14"
watch_apps_js = false
reload_python = false
watch_admin_js = false

[[apps]]
name = "frappe"
repo = "https://github.com/frappe/frappe"
branch = "version-16"

[mariadb]
root_password = "root"

[redis]
port = 13000
```

After `bench init`, run `bench new-site site1.localhost` to create your first site.

---

## Sites and apps after init

Sites and apps are **not** tracked in `bench.toml`. They are managed by commands and discovered from disk:

| What | How to add | Where stored |
|------|-----------|--------------|
| Additional apps | `bench get-app <repo>` | `apps/<name>/` (git clone) + `sites/apps.txt` |
| Sites | `bench new-site <name>` | `sites/<name>/site_config.json` |

`Bench.apps()` scans `apps/` for directories with a `.git` folder. `Bench.sites()` scans `sites/` for directories with a `site_config.json`. Neither reads `bench.toml`.
