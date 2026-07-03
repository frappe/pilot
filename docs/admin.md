# Admin Interface Specification

bench ships a lightweight web-based admin interface built on Flask with no Python dependencies beyond Flask itself. It runs as a process inside the Procfile and starts automatically with `bench start`.

---

## Design constraints

- **Stateless.** The Flask app stores nothing in memory between requests. Every page reads current state from the filesystem (bench.toml, git, log files, site_config.json) or from MariaDB on each request. There is no cache, no background thread.
- **No extra Python dependencies.** Only Flask and the Python standard library. No SQLAlchemy, no Celery, no frontend framework.
- **No frontend framework.** Plain HTML templates with minimal inline CSS. A small amount of vanilla JS is acceptable for auto-refresh and SSE output streaming.
- **Localhost only by default.** Binds to `127.0.0.1` unless overridden.
- **Password always required.** The admin will refuse to start (returning a 503 on all requests) if no password is set in `bench.toml`. There is no unauthenticated mode.

---

## Starting the admin

The admin process is part of the Procfile and starts automatically alongside the web server, workers, and Redis when you run `bench start`. No separate command is needed.

```
admin: PYTHONPATH=<cli_root> .admin-venv/bin/python -m admin.backend.server --bench-root <bench> --port 8002
```

The admin UI is always available at `http://localhost:8002` while the bench is running. To stop it, stop the bench (`bench stop` or Ctrl-C in the `bench start` terminal).

The admin port and password are configured in `bench.toml`:

```toml
[admin]
port = 8002
password = "your-password"
```

`password` is mandatory. If it is missing or empty, the admin UI shows an "Admin Unavailable" error and all API routes return HTTP 503 until a password is configured and the bench is restarted.

---

## Package layout

```
admin/
└── backend/
    ├── app.py                   # Flask app factory — create_app(bench_root: Path)
    ├── server.py                # entry point — started by ProcessManager via Procfile
    │
    ├── readers/                 # Stateless filesystem/DB readers
    │   ├── bench_reader.py      # BenchReader
    │   ├── app_reader.py        # AppReader
    │   ├── site_reader.py       # SiteReader
    │   ├── process_reader.py    # ProcessReader
    │   ├── log_reader.py        # LogReader
    │   └── database_reader.py   # DatabaseReader
    │
    ├── views/                   # Flask blueprints — one per section
    │   ├── dashboard.py         # GET /
    │   ├── apps.py              # GET /apps
    │   ├── sites.py             # GET /sites, /sites/<name>
    │   ├── processes.py         # GET /processes, POST /processes/<name>/restart
    │   ├── logs.py              # GET /logs, /logs/<filename>
    │   ├── database.py          # GET /database/binlogs, /database/slow-queries
    │   ├── tasks.py             # GET /tasks, /tasks/<id>, POST /tasks/run, /tasks/<id>/kill
    │   ├── settings.py          # GET /api/settings/, PATCH /api/settings/
    │   ├── updates.py           # GET /api/updates/, POST /api/updates/apply
    │   └── volume.py            # GET /api/volume/snapshots, POST /api/volume/snapshot
    │
    └── tasks/
        ├── manager/             # Task infrastructure
        │   ├── task_runner.py   # TaskRunner — spawns background job subprocesses
        │   ├── task_reader.py   # TaskReader — reads task state from filesystem
        │   ├── models.py        # TaskInfo dataclass
        │   └── wrapper.py       # subprocess entry point for running jobs
        └── jobs/                # Individual job scripts (OO, one class per file)
            ├── build_assets.py
            ├── get_app_task.py
            ├── install_app_task.py
            ├── new_site_task.py
            ├── drop_site_task.py
            ├── switch_branch_task.py
            └── update_task.py
```

---

## App factory

```python
def create_app(bench_root: Path) -> Flask:
    app = Flask(__name__)
    app.config['BENCH_ROOT'] = bench_root

    app.register_blueprint(dashboard_bp)
    app.register_blueprint(apps_bp,      url_prefix='/apps')
    app.register_blueprint(sites_bp,     url_prefix='/sites')
    app.register_blueprint(processes_bp, url_prefix='/processes')
    app.register_blueprint(logs_bp,      url_prefix='/logs')
    app.register_blueprint(database_bp,  url_prefix='/database')
    app.register_blueprint(tasks_bp,     url_prefix='/tasks')

    return app
```

`bench_root` is injected once at startup and is available to every view via `current_app.config['BENCH_ROOT']`. This is the only persistent state the app holds — it is configuration, not runtime state.

---

## Readers layer

Each reader is instantiated per-request. They have no `__init__`-level side effects beyond storing the path they will read from.

### `BenchReader`

```python
class BenchReader:
    def __init__(self, bench_root: Path): ...

    def config(self) -> BenchConfig:
        """Parse bench.toml. Returns BenchConfig or raises ConfigError."""

    def summary(self) -> BenchSummary:
        """
        Return a lightweight summary struct: bench name, python version,
        process_manager, app count, site count. Reads only bench.toml.
        """
```

```python
@dataclass
class BenchSummary:
    name: str
    python_version: str
    app_count: int
    site_count: int
```

### `AppReader`

```python
class AppReader:
    def __init__(self, bench_root: Path): ...

    def read_all(self) -> List[AppInfo]:
        """
        For each app in bench.toml: check if cloned, read git state, read installed version.
        """

    def read_one(self, app_name: str) -> AppInfo: ...
```

```python
@dataclass
class AppInfo:
    name: str
    repo: str
    branch: str
    is_cloned: bool
    current_commit: str          # short SHA; empty string if not cloned
    commit_message: str          # first line of last commit message
    uncommitted_changes: bool    # True if `git status --porcelain` returns output
    installed_version: str       # from `pip show <name>` Version field; empty if not installed
```

Git state is read by running `git` as a subprocess — no Python git library needed.

### `SiteReader`

```python
class SiteReader:
    def __init__(self, bench_root: Path): ...

    def read_all(self) -> List[SiteInfo]: ...
    def read_one(self, site_name: str) -> SiteInfo: ...
```

```python
@dataclass
class SiteInfo:
    name: str
    exists: bool                 # True if sites/<name>/site_config.json is present
    db_name: str                 # from bench.toml
    db_host: str                 # from site_config.json
    installed_apps: List[str]    # from sites/<name>/site_config.json "installed_apps"
    site_config: dict            # full parsed site_config.json; empty dict if not found
```

### `ProcessReader`

```python
class ProcessReader:
    def __init__(self, bench_root: Path): ...

    def read_all(self) -> List[ProcessInfo]:
        """
        Check pids/ directory for per-process PID files and verify each
        PID is alive via os.kill(pid, 0).
        """
```

```python
@dataclass
class ProcessInfo:
    name: str
    status: str          # 'running' | 'stopped' | 'error' | 'unknown'
    pid: Optional[int]
    uptime: Optional[str]   # e.g. "0:03:12" — only available from supervisor
    log_file: Path
```

### `LogReader`

```python
class LogReader:
    def __init__(self, bench_root: Path): ...

    def list_logs(self) -> List[LogFileInfo]:
        """Scan logs/ directory. Return metadata for each .log file."""

    def read_tail(self, filename: str, lines: int = 200) -> List[str]:
        """
        Return the last N lines of logs/<filename>.
        Raises FileNotFoundError if the file does not exist.
        Validates that filename stays within logs/ (no path traversal).
        """

    def stream_tail(self, filename: str) -> Generator[str, None, None]:
        """
        Yield lines from the end of the file as they are written.
        Used for SSE log streaming. Stops after yielding 5000 lines
        or when the generator is garbage-collected.
        """
```

```python
@dataclass
class LogFileInfo:
    filename: str
    size_bytes: int
    last_modified: datetime
    process_name: str     # derived from filename by stripping .log suffix
```

### `DatabaseReader`

```python
class DatabaseReader:
    def __init__(self, mariadb_config: MariaDBConfig): ...

    def _connect(self) -> Connection:
        """Open a short-lived root connection. Closed after each method call."""

    # Binary log methods
    def list_binary_logs(self) -> List[BinaryLogInfo]:
        """Run SHOW BINARY LOGS."""

    def read_binary_log_events(
        self,
        log_name: str,
        limit: int = 200,
        offset: int = 0,
    ) -> List[BinlogEvent]:
        """Run SHOW BINLOG EVENTS IN '<log_name>' LIMIT <offset>,<limit>."""

    # Slow query methods
    def slow_query_log_path(self) -> Optional[Path]:
        """
        Run SHOW VARIABLES LIKE 'slow_query_log_file'.
        Return the path if slow_query_log is ON, else None.
        """

    def read_slow_queries(self, limit: int = 50) -> List[SlowQuery]:
        """
        Parse the slow query log file from the end.
        Return up to <limit> most recent entries.
        """
```

```python
@dataclass
class BinaryLogInfo:
    log_name: str
    file_size: int

@dataclass
class BinlogEvent:
    log_name: str
    pos: int
    event_type: str
    server_id: int
    end_log_pos: int
    info: str

@dataclass
class SlowQuery:
    timestamp: datetime
    query_time: float      # seconds
    lock_time: float
    rows_examined: int
    rows_sent: int
    user_host: str
    sql: str
```

---

## Routes

### `GET /` — Dashboard

Reads `BenchReader.summary()`, `AppReader.read_all()`, `SiteReader.read_all()`, `ProcessReader.read_all()`. Displays a single-page overview:

- Bench name and Python version
- Apps table: name, branch, short commit hash, uncommitted changes indicator
- Sites table: name, installed apps, DB name, exists flag
- Processes table: name, status (coloured), PID, uptime

### `GET /apps` — Apps list

Full `AppReader.read_all()` output in a table. Shows per-app: repo URL, branch, current commit + message, uncommitted changes, pip-installed version.

### `GET /sites` — Sites list

`SiteReader.read_all()` in a table. Shows: name, exists, installed apps, DB name.

### `GET /sites/<name>` — Site detail

`SiteReader.read_one(name)`. Shows:

- Installed apps list
- Full `site_config.json` rendered as a formatted JSON block
- Action buttons (see Commands section)

### Custom domains (Sites)

Backed by `DomainRouteProvider` (see [docs/production.md](production.md#custom-domain-management)):

- `GET /api/sites/<name>/domains` — `{domains, primary}`
- `POST /api/sites/<name>/domains/dns-records` — step 1 of attaching a domain (CNAME/A options)
- `POST /api/sites/<name>/domains` — step 2, register the domain
- `DELETE /api/sites/<name>/domains` — deregister a domain
- `POST /api/sites/<name>/domains/primary` — set (or clear) the primary domain
- `GET /api/sites/wildcard-domains` / `GET /api/benches/wildcard-domains` — suffixes (no leading `*`) the Create Site and New Bench dialogs build new names from

In the Create Site dialog, the Site Name field is a plain text box when no wildcard domains are configured; with one, it's a prefix field plus a fixed suffix label; with several, a prefix field plus a dropdown to pick the suffix. The New Bench dialog's Admin domain field works the same way.

### `GET /processes` — Process status

`ProcessReader.read_all()`. Shows name, status, PID, uptime, link to its log file.

Process lifecycle is managed by `bench start` / `bench stop`.

### `GET /logs` — Log file list

`LogReader.list_logs()` in a table: filename, process name, size, last modified time.

### `GET /logs/<filename>` — Log viewer

`LogReader.read_tail(filename, lines=request.args.get('lines', 200))`. Renders the lines in a `<pre>` block.

Query parameters:
- `?lines=N` — how many lines to show (default 200, max 5000)
- `?stream=1` — switches the page to live-tail mode (see Streaming section)

### `GET /database/binlogs` — Binary logs list

`DatabaseReader.list_binary_logs()`. Table: log name, file size.

### `GET /database/binlogs/<log_name>` — Binary log detail

`DatabaseReader.read_binary_log_events(log_name, limit, offset)`. Table: pos, event type, server_id, end_log_pos, info. Pagination via `?offset=N&limit=N`.

### `GET /database/slow-queries` — Slow query log

`DatabaseReader.read_slow_queries(limit=50)`. Table: timestamp, query_time, lock_time, rows_examined, rows_sent, user/host, SQL.

Query parameter: `?limit=N` (default 50, max 500).

### `POST /tasks/run` — Execute a command

All command execution goes through the task system (see [specs/tasks.md](tasks.md)). Commands run as detached forked processes; the admin server returns immediately.

Request body (form-encoded):
```
command=migrate&site=site1.localhost
```

Allowed commands are enforced by `TaskRunner._build_argv`. Any unknown command returns HTTP 400. On success, the response is a `303` redirect to `GET /tasks/<task-id>`.

### `GET /tasks` — Task list

See [specs/tasks.md](tasks.md). Lists all tasks, most recent first, with status badges.

### `GET /tasks/<task-id>` — Task detail

See [specs/tasks.md](tasks.md). Shows task metadata, live-streaming output while running, and a kill button for running tasks.

### `GET /api/settings/` — Read current settings

Returns the full settings payload as JSON. The frontend uses this to populate the Settings modal.

```json
{
  "is_linux": true,
  "bench": { "name": "my-bench", "python": "3.14", "http_port": 8000, "socketio_port": 9000 },
  "mariadb": { "host": "localhost", "port": 3306, "admin_user": "root", "socket_path": "", "version": "10.6" },
  "redis": { "cache_port": 13000, "queue_port": 11000, "socketio_port": 12000, "version": "7" },
  "workers": [{ "queues": ["default", "short", "long"], "count": 1 }],
  "nginx": { "http_port": 80, "https_port": 443, "config_dir": "/etc/nginx/conf.d", "worker_processes": "auto", "client_max_body_size": "50m" },
  "letsencrypt": { "email": "", "webroot_path": "/var/www/letsencrypt" },
  "production": { "process_manager": "none", "nginx": false },
  "volume": {
    "enabled": true,
    "pool": "bench-pool",
    "device": "/dev/sdb"
  }
}
```

`is_linux` gates the ZFS Volume tab in the frontend — the tab is only shown on Linux.

### `PATCH /api/settings/` — Update settings

Accepts a JSON body with any subset of the settings sections. Only keys present in the body are updated; omitted keys keep their current values.

```json
{
  "bench": { "http_port": 8080 },
  "workers": [
    { "queues": ["default"], "count": 4 },
    { "queues": ["short", "long"], "count": 1 }
  ]
}
```

**Response:**
```json
{ "ok": true, "restarted": true, "restart_error": null }
```

**Process restart:** If any value in `bench.http_port`, `bench.socketio_port`, `redis.*_port`, `workers.*`, or `production.process_manager` changed, bench regenerates config files and restarts the running process manager (supervisor, systemd, or OpenRC on Alpine) automatically — excluding the admin process itself so the response is delivered before the restart.

**Error responses:**

| Condition | HTTP | Body |
|-----------|------|------|
| JSON parse error | 400 | `{"ok": false, "error": "..."}` |
| Validation failure (port out of range, etc.) | 400 | `{"ok": false, "error": "..."}` |
| bench.toml write failure | 500 | `{"ok": false, "error": "Failed to write config: ..."}` |

---

## Settings modal

The frontend presents settings as a tabbed modal dialog. Tabs are:

| Tab | Editable fields | Read-only fields |
|-----|----------------|-----------------|
| **Bench** | HTTP Port, SocketIO Port | Name, Python version |
| **Appearance** | Theme (light/dark/auto) | — |
| **MariaDB** | — | Host, Port, Admin User, Version, Socket Path |
| **Redis** | Cache Port, Queue Port, SocketIO Port | — |
| **Workers** | Default, Short, Long worker counts | — |
| **Nginx** | Worker Processes, Client Max Body Size, Config Directory | HTTP Port, HTTPS Port |
| **HTTPS** | Enable HTTPS toggle (`admin.tls`), Let's Encrypt email; "Enable HTTPS & issue certificate" action | — |
| **Let's Encrypt** | Email, Webroot Path | — |
| **Production** | Process Manager (none/supervisor + the host's native manager: systemd, or OpenRC on Alpine) | — |
| **Updates** | — | Current version, update availability badge; Update button |
| **ZFS Volume** *(Linux only, dedicated DB only)* | — | Pool Name, Block Device |

MariaDB fields are read-only because the host, port, credentials, and socket path are set once during `bench init` and cannot be meaningfully changed by editing `bench.toml` after the fact — the database server itself is not reconfigured.

The **ZFS Volume** tab and the **Snapshots** page in the sidebar are only shown for benches that use a dedicated MariaDB instance with `volume.enabled = true`. Shared-DB benches hide both.

The Process Manager dropdown lets you switch between `none`, `supervisor`, and the host's native manager — `systemd` on most Linux, `openrc` on Alpine (the backend reports `native_process_manager` so the UI never offers an unavailable option). A change here writes to `bench.toml` and triggers a process restart.

The **HTTPS** toggle sets the server-wide `admin.tls` flag. Enabling it (with a Let's Encrypt email) persists the choice and runs `setup-letsencrypt` to obtain certificates and rewrite nginx with the HTTP→HTTPS redirect; disabling it runs `setup-nginx` to fall back to plain HTTP. `admin.tls` governs HTTPS for both the admin domain and all SSL-enabled sites — per-site SSL is hidden while it is off.

Theme changes are local to the browser session (stored in `localStorage`) and do not touch `bench.toml`.

---

## Log streaming (live tail)

`GET /logs/<filename>?stream=1` returns a page whose JavaScript opens an `EventSource` pointing at `GET /logs/<filename>/stream`.

`GET /logs/<filename>/stream` is a streaming Flask response:

```python
@logs_bp.route('/<filename>/stream')
def stream_log(filename):
    reader = LogReader(current_app.config['BENCH_ROOT'])
    def generate():
        for line in reader.stream_tail(filename):
            yield f"data: {line}\n\n"
    return Response(stream_with_context(generate()), mimetype='text/event-stream')
```

The JavaScript appends each `data:` line to a `<pre>` block and scrolls to the bottom. No library needed — `EventSource` is built into all modern browsers.

---

## Error handling

Views catch `ConfigError`, `FileNotFoundError`, and database connection errors and render a plain error page rather than a 500. This lets the admin remain usable even when the bench is partially broken.

---

## Security notes

- Bind to `127.0.0.1` by default.
- **Password is mandatory.** The admin refuses all requests with HTTP 503 if `[admin] password` is not set in `bench.toml`. There is no way to bypass authentication.
- Sessions are Flask cookie-based. The session key is a random 32-byte hex string generated at startup — sessions are invalidated on process restart.
- `bench generate-admin-session` issues a 5-minute, single-use `?sid=` token that the frontend exchanges for a 1-day `HttpOnly` session cookie — an alternative to password login, signed with `admin.jwt_secret` in `bench.toml`. See [docs/commands.md](commands.md#bench-generate-admin-session).
- `bench issue-site-token` issues a scoped JWT (`scope: "site"`) for programmatic site-to-bench API calls. The token is restricted to the named site and cannot access other sites or bench-level endpoints. Use it as `Authorization: Bearer <token>`. See [docs/commands.md](commands.md#bench-issue-site-token).

- `LogReader.read_tail` and `stream_tail` validate that the requested filename contains no path separators and resolves to a file inside `logs/`. Any traversal attempt returns HTTP 400.
- Command execution uses `TaskRunner._build_argv`, which only accepts whitelisted commands. No user-supplied string is passed to a shell.
- `task_id` values are validated against `^\d{8}-\d{6}-[0-9a-f]{6}$` before being used as directory names.
- Root MariaDB credentials come from `bench.toml` — the admin must be run by a user who can read that file.

---

### Site-to-bench API

When a site is created through the admin UI, the bench automatically writes two keys into the site's `site_config.json`:

- `pilot_endpoint` — the admin URL of the bench (e.g. `https://admin.example.com`)
- `pilot_auth_token` — a scoped JWT (`scope: "site"`) valid for 365 days, restricted to that site

The site can use these to call the bench admin API directly — no user login required. For example, from a Frappe hook or background job:

```python
import frappe

endpoint = frappe.get_site_config().get("pilot_endpoint")
token = frappe.get_site_config().get("pilot_auth_token")
headers = {"Authorization": f"Bearer {token}"}

# Install an app on this site
requests.post(f"{endpoint}/api/sites/{frappe.local.site}/install-app",
              json={"app_name": "my-app"}, headers=headers)

# Enable SSL
requests.post(f"{endpoint}/api/sites/{frappe.local.site}/enable-ssl",
              json={}, headers=headers)

# Set backup schedule
requests.post(f"{endpoint}/api/sites/{frappe.local.site}/backup-schedule",
              json={"cron": "0 2 * * *"}, headers=headers)
```

The token is scoped — it can only act on its own site. Any attempt to access a different site or bench-level endpoints (e.g. `/api/benches`) returns 403.

Both keys are in `PROTECTED_CONFIG_KEYS` — they are never exposed in the admin UI and are preserved across config edits.

---

## Marketplace

`GET /api/apps/registry` returns the full `registry/apps.json` array. The Marketplace page reads this endpoint alongside `GET /api/apps/` (installed apps) to render the app list.

Each registry entry has:

```json
{
  "name": "erpnext",
  "title": "ERPNext",
  "description": "Open source ERP",
  "repo": "https://github.com/frappe/erpnext",
  "branch": "version-16",
  "branches": ["version-15", "version-16"],
  "logo_url": "https://cloud.frappe.io/files/erpnext-blue.png",
  "website": "https://frappe.io/erpnext",
  "documentation": "https://docs.frappe.io/erpnext",
  "categories": ["Accounting", "Business", "Featured"],
  "category": "Applications",
  "stars": 35439
}
```

**`category`** is one of six values: `Applications`, `Extensions`, `Integrations`, `Compliance`, `Developer Tools`, `Utilities`. The frontend sidebar filters by this field.

Apps whose `repo` is under `github.com/frappe/` are sorted to the top by `stars` and labelled "From Frappe". All others appear below under "Community".

Clicking **Add** on an app with a `repo` posts to `POST /api/apps/add` with `{ name, repo, branch }` and redirects to the resulting task.

---

## Setup wizard API

When a bench has not been initialized (`config/Procfile` is missing), `bench start` launches a standalone wizard server instead of the normal admin. The wizard exposes these endpoints:

### `GET /api/setup/config`

Returns the current `bench.toml` values pre-populated in the wizard form, plus environment metadata:

```json
{
  "bench_name": "my-bench",
  "is_linux": true,
  "available_devices": [{ "path": "/dev/sdb", "size_bytes": 107374182400 }],
  "rootfs_free_bytes": 42949672960,
  "mariadb_instance": "my-bench",
  "mariadb_admin_user": "root",
  "volume_enabled": false,
  ...
}
```

### `POST /api/setup/validate-mariadb`

Checks whether the supplied credentials will work before the wizard proceeds.

**Request body:**
```json
{
  "mariadb_password": "secret",
  "mariadb_admin_user": "root",
  "dedicated_db": false
}
```

**Behaviour:**
- `dedicated_db: true` (dedicated instance) — if the instance is not yet provisioned, returns `will_install` without attempting a connection; init will create the instance with the supplied password.
- `dedicated_db: false` (shared system MariaDB) — always attempts a connection using the system socket; returns `valid` or `invalid`.
- If MariaDB is not installed at all, returns `will_install`.

**Response:** `{ "state": "valid" | "invalid" | "will_install" }`

### `POST /api/setup/save`

Writes the wizard form data to `bench.toml`. The wizard sends the final payload after the last configuration step. Unknown keys are ignored; existing keys not in the payload are preserved.

### `POST /api/setup/init`

Kicks off `bench init` as a background task. Returns `{ "ok": true, "task_id": "..." }`. Progress is streamed via `GET /api/setup/stream/<task_id>` (SSE).

---

## CLI commands

- **`bench build-admin`** — rebuilds the admin frontend static assets. Run this after pulling admin UI changes. The server itself is managed by `bench start` / `bench stop` — no separate start/stop commands exist.

Admin lifecycle is owned by `ProcessManager`: the `admin:` entry is written into `config/Procfile` during `bench init`, and the process is started and stopped alongside all other bench processes.
