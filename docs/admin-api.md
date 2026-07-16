# Admin API Specification

bench ships a web-based admin backend built on Flask. It exposes a versioned JSON API under `/api/v1` and serves a compiled single-page frontend for everything else. It runs as a process managed by `bench start` / `bench stop`. See [admin-ui.md](admin-ui.md) for the frontend that consumes this API.

---

## Design constraints

- **Stateless.** The Flask app keeps no request state in memory. Every route reads current state from the filesystem (`bench.toml`, git, `site_config.json`, task directories) or from the database on each request.
- **Admin-only dependencies.** The `pilot` core CLI has zero dependencies. The Flask backend is installed via the `admin` extra (`pip install .[admin]`): Flask, psutil, PyMySQL, gunicorn, boto3, psycopg2-binary, and `pyjwt[crypto]`. These live only under `admin/backend/` and its own virtualenv (`.admin-venv/`); the CLI itself never imports them.
- **Compiled single-page frontend.** The UI is a Vite-built SPA under `admin/frontend/`, compiled to `admin/backend/static/dist/`. Flask serves those static assets and falls back to `index.html` for client-side routes; it serves a 503 with a build instruction if `bench build-admin` hasn't produced a build yet. Unknown paths under `/api/` never fall back to the SPA — they return a JSON 404.
- **Not loopback-restricted by the process itself.** `admin.backend.run_server` binds a dual-stack socket on all interfaces (`app.run(host="::")`), so restricting access to localhost or a private network is the job of the reverse proxy and the bench's `[firewall]` rules, not a bind-address restriction in the admin process.
- **Password always required.** The admin refuses every `/api/` request with HTTP 503 (`admin_disabled` or `session_unavailable`) if `[admin] enabled` is false or no password is set in `bench.toml`, except for the small set of endpoints marked open (health, bootstrap, session) and setup endpoints while the bench has no `bench.toml` yet.

---

## Starting the admin

The admin process is part of the Procfile and starts automatically alongside the web server, workers, and Redis when you run `bench start`. No separate command is needed.

In production (`bench start`, process-manager-driven) it runs under gunicorn:

```
admin: <cli_root>/.admin-venv/bin/gunicorn -c <bench>/config/admin-gunicorn.conf.py admin.backend.wsgi:application
```

In dev/watch mode it runs the module directly, with auto-reload:

```
admin: PYTHONPATH=<cli_root> <cli_root>/.admin-venv/bin/python -m admin.backend.run_server --bench-root <bench> --port 8002 --dev
```

The admin UI is always available at `http://localhost:<admin.port>` (default `8002`) while the bench is running. The port and password are configured in `bench.toml`:

```toml
[admin]
port = 8002
password = "your-password"
```

`password` is mandatory. If it is missing or empty, every `/api/` route returns HTTP 503 until a password is configured.

---

## Package layout

```
admin/
├── frontend/                    # Vite/React SPA source; builds into backend/static/dist
└── backend/
    ├── app.py                   # Flask app factory — create_app(bench_root: Path): config +
    │                               extensions + install_auth_guard() + every
    │                               app.register_blueprint() call + the built-frontend
    │                               fallback route + install_api_error_handlers()
    ├── middleware.py             # AuthPolicy, install_auth_guard() (the before_request
    │                               guard), session/JWT verification, site-scope checks,
    │                               client_ip(), rate_limit()
    ├── run_server.py             # runs the admin with its own Werkzeug server (dev
    │                               reload, wizard bootstrap) — the runserver equivalent
    │                               of wsgi.py
    ├── wsgi.py                  # gunicorn entry point used in production
    ├── internal/                 # admin-only helpers with no Flask coupling
    │   ├── jwks.py               # remote JWKS verification for external issuers
    │   └── rate_limiter.py       # SlidingWindow, UsedTokens — in-memory counters
    │
    ├── api/
    │   ├── routes.py             # API_ROOT_PREFIX/API_V1_PREFIX, is_api_path() — the
    │   │                           routing-shape contract everything else builds on
    │   ├── errors.py             # ApiProblem, install_api_error_handlers
    │   ├── responses.py          # response envelope, pagination, accepted_task_response()
    │   └── v1/                   # Flask blueprints, one per API area
    │       ├── core.py               # health, bootstrap, session
    │       ├── setup.py               # first-run setup wizard API
    │       ├── apps.py, git.py        # installed apps, marketplace, git connection
    │       ├── benches.py             # multi-bench management
    │       ├── sites/                 # sites_bp, split by sub-resource
    │       │   ├── shared.py              # site_name(), error helpers shared by every file below
    │       │   ├── core.py                # list/detail/create/drop + reinstall/clear-cache/migrate,
    │       │   │                             quick site login (sid-in-URL redirect)
    │       │   ├── apps.py                # GET/POST/DELETE /<name>/apps...
    │       │   ├── domains.py             # /<name>/domains..., enable-tls
    │       │   ├── configuration.py       # /<name>/configuration (public config filtering)
    │       │   └── backups.py            # /<name>/backups..., /<name>/backup-schedule
    │       ├── processes.py           # runtime/process control
    │       ├── logs.py, databases.py  # log and database inspection
    │       ├── tasks.py                # task queue + task worker control
    │       ├── settings.py             # bench.toml read/patch, audit log, client IP
    │       └── ssh_keys.py, stats.py, updates.py
    │
    └── providers/                # Stateless filesystem/DB providers, one per resource
        ├── bench.py             # BenchProvider — reachability, TLS cert, process status
        ├── apps.py              # AppProvider — cloned apps + git/pip state
        ├── sites.py             # SiteProvider — sites + site_config.json
        ├── processes.py         # ProcessProvider — process status/PID/resource use
        ├── logs.py              # LogProvider — log listing, tail, streaming
        ├── backups.py           # BackupProvider — on-disk and offsite backup sets
        ├── monitor.py           # MonitorProvider — monitor log history
        └── os.py                # OSProvider — CLI/frappe/runtime version info, host-level facts

pilot/internal/                  # Zero-dependency helpers shared across the whole CLI
├── validators.py                # Regex validators (site/app names, cron, branch, email)
├── site_paths.py                # Symlink-safe resolution of a site's directory
└── atomic_file.py, git.py, toml.py, site_session.py

pilot/managers/cron.py           # CronManager — one crontab entry per (bench, job_key)

pilot/tasks/                     # The task engine itself (Flask-free) — see docs/tasks.md
├── manager/                     # Task infrastructure
│   ├── task_runner.py           # TaskRunner — validates + queues a task
│   ├── task_reader.py           # TaskReader — reads task state from disk
│   ├── task_store.py            # durable queue + idempotent creation
│   ├── worker_registry.py, worker.py, worker_state.py  # single task worker
│   ├── events.py                # SSE event shapes for task streaming
│   └── models.py                # TaskInfo dataclass
└── jobs/                        # One class per background job (get_app_task.py, new_site_task.py, ...)
```

---

## The `/api/v1` contract

### Versioning policy

- Every product route is registered under one prefix, `API_V1_PREFIX = "/api/v1"`. There is currently no `/api/v2` and no unversioned product route.
- A future breaking contract change is introduced as `/api/v2` served alongside `/api/v1`, not as a branch inside existing view logic — routes, payloads, errors, pagination, and event shapes are versioned together as one unit, and a new version does not fork request handling on the requested version string.
- When a route migrates to a new version, the old one is removed in the same change; there is no staged deprecation window with two live copies of a route.
- Unknown paths under `/api/` return a JSON 404 or 405 (`_handle_method_not_allowed` in `api/errors.py` / the `serve_frontend` guard in `app.py`) and never fall through to the single-page app.
- Whether an endpoint requires authentication is metadata on the view function (`AuthPolicy`), not something inferred from its path.

### Response envelope

A successful response is either the resource's JSON directly, or `{"data": [...], "meta": {...}}` for a paginated collection. There is no `{"ok": true}` wrapper.

Every error uses one shape, produced by `error_response()` in `api/responses.py`:

```json
{
  "error": {
    "code": "site_not_found",
    "message": "Site not found.",
    "details": {}
  }
}
```

`code` is a stable machine-readable string; `message` is for humans; `details` is `{}` unless the error carries extra structured context (for example `{"needs_email": true}` on a certificate-email error, or `{"token_invalid": true}` on a rejected git token).

Status codes and where they're actually used:

| Status | Meaning | Example |
|--------|---------|---------|
| 200 | Read succeeded, or a synchronous write completed | `GET /sites/{name}`, `PATCH /settings` |
| 201 | A resource was created synchronously, with `Location` | `POST /session` (login), `POST /ssh-keys`, `POST /sites/{name}/login` |
| 202 | Work was queued as a task; body is the task, `Location` points at it | `POST /sites`, `POST /apps`, `POST /tasks` |
| 204 | Deletion (or logout) completed; no body | `DELETE /session`, `DELETE /sites/{name}/backup-schedule`, `DELETE /ssh-keys/{fingerprint}` |
| 400 | Malformed request body (not a JSON object) | any `error_response("malformed_request", ...)` |
| 401 | Missing/invalid credentials or session | `invalid_credentials`, `invalid_login_token`, `git_auth_required` |
| 403 | Authenticated but not authorized | wrong JWT scope, `bench_management_forbidden` |
| 404 | Resource does not exist | `site_not_found`, `task_not_found`, `domain_not_found` |
| 409 | Lifecycle conflict or duplicate | `task_conflict`, `bench_busy`, `tls_already_enabled`, `app_already_installed` |
| 422 | Well-formed request, invalid field values | `invalid_bench_name`, `invalid_schedule`, `missing_app` |
| 429 | Rate limit exceeded | login, login-link issuance |
| 500 / 503 | Internal error / dependency unavailable | `settings_unavailable`, `configuration_unavailable` |

Timestamps are UTC ISO 8601. Canonical URLs never end in a trailing slash. Structured event streams live under `/events`; raw/downloadable payloads live under `/content`.

### Auth model

Every view has an `AuthPolicy`, set by a decorator in `admin/backend/middleware.py`:

| Policy | Decorator | Meaning |
|--------|-----------|---------|
| `AUTHENTICATED` (default) | none — implicit | Requires a valid session cookie or bearer token |
| `OPEN` | `@allow_unauthenticated` | No auth check at all (health, bootstrap, session endpoints) |
| `SETUP_CONDITIONAL` | `@allow_during_setup` | Open only while the bench has no admin password yet; once one is set, behaves like `AUTHENTICATED` |

`middleware.py`'s `install_auth_guard()` registers a `before_request` hook that runs for every `/api/` path and **fails closed**: any exception loading `bench.toml` returns 503 rather than falling back to open access (the one exception is `SETUP_CONDITIONAL` before `bench.toml` exists at all, which is the wizard's only way to bootstrap). Once authenticated, `get_authorization_error()` checks the request's JWT claims against the view:

- Routes registered under `benches_bp` / `bench_readiness_bp` additionally require `admin.allow_bench_management` to be true (`guard_bench_management`, a blueprint-level `before_request`), else every route in that area returns 403.
- Routes decorated with `@require_scope(...)` (most of `sites_bp`, including `create_login_link`) require the caller's JWT to either carry no site restriction (`scope: "bench"`) or match the specific site in the URL (`scope: "site"`, confined by the `site` claim). A JWT confined to one site can never reach another site's routes or any bench-level route.

Sensitive endpoints add a sliding-window rate limit on top (`@rate_limit`): `POST /session` (5/60s per IP) and `POST /sites/{name}/login` (10/60s per IP).

### Pagination

Growing collections use cursor pagination via `parse_pagination()` / `paginated_response()` in `api/responses.py`:

- Request: `?limit=N&cursor=<opaque>`. An invalid or out-of-range `limit`/`cursor` silently falls back to the default rather than erroring — pagination inputs are advisory.
- Response: `{"data": [...], "meta": {"limit": N, "next_cursor": "<opaque>" | null}}`. `next_cursor` is `null` once there's nothing more to fetch.

Currently only `GET /audit-events` uses this (default limit 50, max 500); it also accepts `type`, `site`, and `status` filters that are independent of pagination.

### Task representation

A task is the JSON produced by `TaskInfo.as_dict()`:

```json
{
  "task_id": "20260716-140501-a1b2c3",
  "command": "migrate",
  "args": { "site": "site1.localhost" },
  "status": "running",
  "pid": 48213,
  "queued_at": "2026-07-16T14:05:01+00:00",
  "started_at": "2026-07-16T14:05:01+00:00",
  "finished_at": null,
  "exit_code": null,
  "duration_seconds": null,
  "queue_position": null,
  "failure": null
}
```

- `status` is one of `queued`, `running`, `success`, `failed`, `killed` (`TaskStatus`). Valid transitions: `queued → running | killed`, `running → success | failed | killed`; terminal states don't transition further.
- `args` is redacted for display: password- and token-like keys are replaced with `"[redacted]"`, and credentials embedded in URLs are stripped, before the task is ever written to disk (`public_task_args`). A few commands (`new-site`, `new-site-from-backup`, `reinstall-site`) additionally keep their password argument out of `meta.json` entirely, in a private `secrets.json` the wrapper reads at exec time.
- `failure` is populated only when `status == "failed"`, as `{"code": "command_failed" | "task_interrupted", "message": "..."}`.
- Every mutating action that starts background work (`POST /sites`, `POST /apps`, `POST /sites/{name}/actions/*`, `POST /setup/actions/start`, ...) returns **202** with the task JSON above and a `Location: /api/v1/tasks/{task_id}` header, built by `accepted_task_response()`.
- **Idempotency:** send `Idempotency-Key: <opaque>` on a task-creating POST to make retried requests safe. The key is hashed and checked against a fingerprint of `{command, args}`; a repeat with the same key and same request body returns the original task instead of creating a new one. `POST /setup/actions/start` requires this header (422 `idempotency_key_required` if missing); it's optional elsewhere.
- **Conflicts:** site-scoped mutations pass a `resource_key` (`site:{name}`) so only one task can be in flight per site; a second concurrent request gets 409 `task_conflict`.
- **Retry:** `POST /tasks/{task_id}/actions/retry` resubmits the same `command`/`args` as a new task. It refuses (409 `task_not_finished`) if the task is still active, and refuses (409 `fresh_credentials_required`) for commands whose secret arguments (like a generated admin password) aren't retained — `new-site`, `new-site-from-backup`, `reinstall-site`.
- **Cancel:** `DELETE /tasks/{task_id}` kills a queued or running task (409 `task_not_active` otherwise).
- **Task worker:** the single background worker that drains the task queue is itself a resource — `GET /task-worker` reports `{"active", "uncertain", "status", "desired", "queued_tasks", "running_tasks"}`; `POST /task-worker/actions/start` and `.../stop` set the desired intent and return 202 with that same resource.

### SSE / events

Two endpoints stream Server-Sent Events, both as one JSON object per `data:` line rather than raw text.

**`GET /tasks/{task_id}/events`** — task output and lifecycle, replayable via `Last-Event-ID`:

```
id: 42
data: {"type":"line","line":"Updating site1.localhost..."}

data: {"type":"status","status":"running","queue_position":null}

data: {"type":"done","status":"success","exit_code":0,"failure":null}
```

Event `type` is one of `line` (a line of output), `overwrite` (replace the last line — used for progress bars), `status` (queue position or state change), or `done` (terminal, sent once). Only `line`/`overwrite`/`done` events carry an `id:` for resumption; `status` events don't, since they're not meant to be replayed. Headers disable proxy buffering (`X-Accel-Buffering: no`).

**`GET /logs/{filename}/events`** — live log tail:

```
data: {"line": "2026-07-16 14:05:01 INFO Starting worker..."}
```

On an invalid filename the stream emits one `{"error": "..."}` message instead of an HTTP error, since the response has already switched to `text/event-stream`. A stream stops after 5000 lines or when the client disconnects.

---

## Full route reference

All paths below are relative to `/api/v1`. "Auth" combines the `AuthPolicy` with any additional guard (bench-management, site-scope).

### Bootstrap and session

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/health` | open | Liveness probe; always `{"status": "ok"}`, CORS-open |
| GET | `/bootstrap` | open | Tells the SPA which mode to render: `{"mode": "setup"}` before the bench is initialized/passworded, or `{"mode": "admin", "enabled", "name", "db_type", "production", "native_process_manager", "allow_bench_management", "task_worker"}` once it's live |
| GET | `/session` | open | `{"authenticated": bool, "scope"?: "bench"\|"site"}` for the current cookie/bearer credential |
| POST | `/session` | open, rate-limited | Log in. Body `{"password": "..."}` or `{"sid": "<jwt>"}` (one-time sign-in token). Sets the `sid` `HttpOnly` cookie; 201 with `{"authenticated": true, "scope": "bench"}` |
| DELETE | `/session` | open | Log out; clears the `sid` cookie; 204 |

### Setup wizard

Available before `bench.toml`/the admin password exist (`SETUP_CONDITIONAL`); becomes fully authenticated once a password is set.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/setup/configuration` | Current `bench.toml` values pre-filled for the wizard form, plus `running_setup_task_id` and `*_configured` flags for password fields (never the passwords themselves) |
| PUT | `/setup/configuration` | Write wizard form fields to `bench.toml`; issues the session cookie once `admin_password` is set |
| GET | `/setup/framework-branches` | `{"branches": [...]}` — selectable Frappe framework branches |
| POST | `/setup/database-validations` | Body `{"engine": "mariadb"\|"postgres", ...credentials}`; returns `{"engine", "state": "valid"\|"invalid"\|"will_install"}` without starting `init` |
| POST | `/setup/actions/start` | Requires `Idempotency-Key`; kicks off `bench init` as a task (`wizard-setup`), 202 with the task |
| POST | `/setup/actions/finish` | Body `{"task_id": "..."}`; verifies the setup task succeeded and the bench is fully initialized, then 204. In the standalone wizard process this also schedules the wizard server's own shutdown |

### Apps, marketplace, updates

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/apps` | All cloned apps: repo, branch, commit, `has_local_changes`, installed version |
| POST | `/apps` | Install an app. Body `{"name"?, "repo"?, "branch"?, "sites"?: [...]}` — a bare `name` installs an already-cloned app; `repo` clones by URL; either fetches onto `sites` too if given. 202 (task `get-app` or `get-and-install-app`); 409 `app_already_installed` if already cloned |
| GET | `/apps/{name}` | One app's detail; 404 if not cloned |
| PATCH | `/apps/{name}` | Body `{"repo": "..."}`; rewrites the app's git remote (`origin`) |
| DELETE | `/apps/{name}` | 202, task `remove-app` |
| POST | `/apps/fetch` | 202, task `fetch-all-app-updates` — fetches every cloned app's remote in the background |
| GET | `/marketplace/apps` | Full app registry (name, repo, branches, logo, categories, stars) for the catalog view — distinct from installed apps |
| GET | `/app-updates` | Per-app commits-ahead/behind against its remote, from the **last** fetch (no network call) |
| POST | `/app-update-checks` | Same shape as `/app-updates`, but runs `git fetch` synchronously first — a request-blocking network refresh, not a task |
| GET | `/cli-updates` | The CLI's own commits-behind status against its remote (no network call) |
| POST | `/cli-update-checks` | Same, after a synchronous `git fetch` |

`POST /apps/fetch` and `POST /app-update-checks` both refresh git state but differ in shape: the former is an async task covering every app; the latter is a synchronous, per-request check used to populate the updates view immediately.

### Benches (multi-bench management)

All routes 403 (`bench_management_forbidden`) when `admin.allow_bench_management` is false.

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/benches` | Every sibling bench directory next to this one: name, port, domain, `production`, `reachable`, `site_count`, `admin_url` |
| POST | `/benches` | Create a new bench. Body `{"name", "process_manager", "db_type"?, "admin_domain", "admin_tls"?}`. In a production parent, starts the new bench's admin over nginx immediately and returns 201 with `wizard_at_domain: true`; otherwise spawns a standalone wizard server on its own port and returns 201 with `wizard_at_domain: false` |
| GET | `/benches/{name}` | One bench's resource (same shape as the list) |
| DELETE | `/benches/{name}` | Drop a bench; 409 `bench_not_empty` if it still has sites, 204 on success |
| POST | `/benches/{name}/actions/start` | Start a production bench's workload via its process manager; returns the bench resource |
| POST | `/benches/{name}/actions/stop` | Stop it |
| POST | `/benches/{name}/actions/restart` | Restart it |
| GET | `/benches/domain-options` | Wildcard domain suffixes (no leading `*`) the New Bench dialog can build an admin domain from |
| POST | `/bench-readiness-checks` | Body `{"domain", "scheme"?}` or `{"port"}`; probes whether a new bench's wizard/admin is reachable yet — `{"ready": bool}` |

Bench-lifecycle routes (`create`, `delete`, `actions/*`) take an exclusive file lock per bench and a bench-management-wide lock; a concurrent request against the same bench gets 409 `bench_busy`.

### Sites

| Method | Path | Auth | Purpose |
|--------|------|------|---------|
| GET | `/sites` | authenticated | All sites: `name`, `exists`, `installed_apps`, `framework_branch`, `broken`, `provisioning` |
| GET | `/sites/wildcard-domains` | authenticated | Wildcard suffixes the Create Site dialog can build a name from |
| POST | `/sites` | authenticated | Create a site. Body `{"name", "apps"?: [...]}`; a random admin password is generated server-side. 202, task `new-site` with rollback callbacks that remove a failed/canceled site |
| GET | `/sites/{name}` | site-scope | Site detail plus `ssl`, `installable_apps`, `http_port`, `nginx_enabled`, `admin_tls` |
| DELETE | `/sites/{name}` | site-scope | 202, task `drop-site` |
| POST | `/sites/{name}/actions/reinstall` | site-scope | Body `{"admin_password"?}` (generated if omitted); 202, task `reinstall-site` |
| POST | `/sites/{name}/actions/clear-cache` | site-scope | 202, task `clear-cache` |
| POST | `/sites/{name}/actions/migrate` | site-scope | 202, task `migrate` |
| POST | `/sites/{name}/actions/enable-tls` | site-scope | Body `{"email"?}` (falls back to the bench's Let's Encrypt email); 409 `tls_already_enabled` if already on; 202, task `setup-letsencrypt` with a rollback callback |
| GET | `/sites/{name}/apps` | site-scope | Installed apps with title/description/branch/commit/version |
| POST | `/sites/{name}/apps` | site-scope | Install an app on the site. Body `{"app"?, "repo"?, "branch"?}` — a cloned app installs directly (task `install-app`); otherwise it's fetched first (task `get-and-install-app`) |
| DELETE | `/sites/{name}/apps/{app}` | site-scope | `?force=true` to skip dependency checks; 202, task `uninstall-app` |
| GET | `/sites/{name}/domains` | site-scope | `{"domains": [...], "primary": "..."}` |
| POST | `/sites/{name}/domains` | site-scope | Body `{"domain"}`; registers it, then 202 for the nginx/cert task that applies it |
| GET | `/sites/{name}/domains/{domain}` | site-scope | `{"domain", "is_primary"}`; 404 if not attached |
| PATCH | `/sites/{name}/domains/{domain}` | site-scope | Only `{"primary": true}` is accepted; sets it primary, 202 for the nginx task |
| DELETE | `/sites/{name}/domains/{domain}` | site-scope | Deregister; 202 for the nginx task |
| GET | `/sites/{name}/domains/{domain}/dns-records` | site-scope | Read-only CNAME/A record guidance for attaching the domain |
| GET | `/sites/{name}/configuration` | site-scope | Allowlisted `site_config.json` fields (protected/secret-like keys filtered out) |
| PATCH | `/sites/{name}/configuration` | site-scope | Merge-patch the allowlisted fields; `null` removes a key; rejects touching protected/secret-like keys (422 `protected_configuration`) |
| GET | `/sites/{name}/backups` | site-scope | `?limit=N` (default 20), newest-first backup sets with their files |
| POST | `/sites/{name}/backups` | site-scope | 202, task `backup-site` (always `--with-files`) |
| GET | `/sites/{name}/backups/{timestamp}` | site-scope | One backup set; 404 if unknown |
| GET | `/sites/{name}/backups/{timestamp}/files/{file_id}/content` | site-scope | Streams the backup file as an attachment; validates `file_id` stays within that backup's directory |
| GET | `/sites/{name}/backups/{timestamp}/download-links` | site-scope | Pre-signed S3 URLs for an offsite backup's files, so the client downloads straight from the bucket |
| GET | `/sites/{name}/backup-schedule` | site-scope | `{"schedule": "<cron>"\|null, "retention": {...}\|null}` |
| PUT | `/sites/{name}/backup-schedule` | site-scope | Body `{"schedule", "retention"?}`; validates the cron expression and retention config, writes both, returns the updated schedule |
| DELETE | `/sites/{name}/backup-schedule` | site-scope | Removes the cron entry and clears retention; 204 |
| POST | `/sites/{name}/login` | site-scope, rate-limited | Creates a real Frappe Administrator session for the site synchronously and returns it as a redirect URL; 201 `{"url": "<site origin>/desk?sid=<sid>"}`. The site's own application layer is responsible for turning that `sid` into a cookie — the admin backend's job ends at handing back the URL |

`POST /sites`, `DELETE /sites/{name}`, and the `reinstall`/`clear-cache`/`migrate`/`enable-tls` actions all accept `Idempotency-Key` and are serialized per site via a `site:{name}` resource key (409 `task_conflict` on overlap).

### Tasks and task worker

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/tasks` | All tasks, newest first. `?status=queued\|running\|success\|failed\|killed` filters |
| POST | `/tasks` | Generic task creation. Body `{"command", ...args}` against the same whitelist `TaskRunner` uses everywhere else; 202. Accepts `Idempotency-Key` |
| GET | `/tasks/{task_id}` | One task's detail |
| DELETE | `/tasks/{task_id}` | Cancel a queued or running task; 204. 409 `task_not_active` if already finished |
| POST | `/tasks/{task_id}/actions/retry` | Resubmit the task's command/args as a new task; 202. 409 if still active or if its secrets weren't retained |
| GET | `/tasks/{task_id}/events` | SSE task output (see above) |
| GET | `/tasks/{task_id}/output/content` | Full `output.log` as a downloadable attachment; 404 if not yet written |
| GET | `/task-worker` | `{"active", "uncertain", "status", "desired", "queued_tasks", "running_tasks"}` |
| POST | `/task-worker/actions/start` | Set the desired worker intent to running and wake it; 202 with the worker resource |
| POST | `/task-worker/actions/stop` | Set the desired intent to stopped and wake it; 202 |

### Runtime (processes)

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/runtime/processes` | Every managed process: name, status, PID, uptime, CPU%, RSS/PSS memory, log filename; `production: bool` |
| POST | `/runtime/actions/start` | Production only (409 otherwise); `supervisorctl start` for the bench's program group |
| POST | `/runtime/actions/stop` | Production only; stops every non-admin program |
| POST | `/runtime/actions/restart` | Production only; restarts every non-admin program |

Start/stop/restart deliberately exclude the admin's own supervisor program, so the request that triggers the restart can still complete.

### Logs

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/logs` | Every `.log` file under `logs/`: filename, size, last modified, process name, line count |
| GET | `/logs/{filename}` | Tail as JSON. `?lines=N` (default 200, max 5000), `?search=text` (case-insensitive substring filter applied after the tail) |
| GET | `/logs/{filename}/content` | Full file as a downloadable `text/plain` attachment |
| GET | `/logs/{filename}/events` | SSE live tail (see above); stops after 5000 lines |

`filename` is validated to resolve to a plain file directly inside `logs/`; any path separator or traversal attempt returns 422 `invalid_log`.

### Database

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/database/sites` | Sites with a configured database, for the query tool's site picker |
| GET | `/database/schema` | `?site=` required; that site's table/column schema |
| POST | `/database/queries` | Body `{"site", "query", "read_only"?: true}`; runs the query against that site's database and returns `{"columns", "rows", "row_count", "duration_ms", "truncated", "affected_rows"}` |

### Git

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/git/connection` | Connection status: `{"connected", "provider"?, "username"?, "token_preview"?, "is_token_valid"?, "token_expires_at"?, "providers"}` |
| PUT | `/git/connection` | Body `{"provider", "token", "username"?, "expires_at"?}`; validates the token against the provider before saving (idempotent — replaces any existing connection) |
| DELETE | `/git/connection` | Disconnect; 204 |
| GET | `/git/repositories` | Repositories visible to the connected account; 401 `git_auth_required` if not connected |
| GET | `/git/branches` | `?repo=` required; `{"branches", "default_branch"}`, default branch listed first |
| POST | `/git/repository-resolutions` | Body `{"repo", "branch"?}`; resolves the Frappe app name from a repository without cloning it |

### Settings, audit, network

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/settings` | The full current-bench settings resource: bench, mariadb, postgres, redis, workers, firewall, production, admin, letsencrypt, s3 (secrets never echoed back), monitor |
| PATCH | `/settings` | Merge-patch by section; regenerates configs and restarts affected services (excluding the admin process) when a restart-triggering field changes; 422 `invalid_settings` on a rejected value |
| GET | `/audit-events` | Paginated bench-wide audit log (see Pagination); `?type=&site=&status=` filters |
| GET | `/network/client` | `{"ip": "..."}` — the requester's own address, for firewall allow-listing |

### SSH keys

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/ssh-keys` | `{"keys": [{"fingerprint", "type", "comment"}, ...]}` |
| POST | `/ssh-keys` | Body `{"public_key"}`; 201 with `Location: /api/v1/ssh-keys/{fingerprint}`; 409 if already authorized |
| DELETE | `/ssh-keys/{fingerprint}` | 204; 409 `ssh_key_removal_rejected` if it's the last key |

### Monitoring

| Method | Path | Purpose |
|--------|------|---------|
| GET | `/monitor/status` | System/application monitor log file paths and last-modified times |
| GET | `/monitor/history` | Parsed monitor history, `?window=1h` (or other supported window) |
| GET | `/system` | Static host facts: disk/memory/swap totals, CPU count, kernel/OS version, runtime versions |
| GET | `/metrics` | Live point-in-time metrics: CPU/memory/disk usage and breakdowns, network/disk I/O rates, directory sizes |

---

## Security notes

- The admin process itself is not loopback-restricted (see Design constraints); production deployments rely on nginx as the single entry point plus the bench's firewall rules.
- **Password is mandatory.** Every `/api/` route returns HTTP 503 if `[admin] password` is unset.
- Sessions are `HttpOnly`, `SameSite=Lax` cookies carrying a signed JWT (`admin.jwt_secret` in `bench.toml`), valid for 24 hours.
- `bench generate-admin-session` issues a 5-minute, single-use sign-in token; the frontend exchanges it via `POST /api/v1/session` with `{"sid": "<jwt>"}` for the 1-day session cookie.
- `bench issue-site-token` issues a scoped JWT (`scope: "site"`) restricted to one site, for programmatic site-to-bench calls (`Authorization: Bearer <token>`).
- `admin.jwks_url` lets a remote issuer mint session/bearer tokens with its own key pair, verified against fetched public keys — see below.
- Log filenames are validated to resolve inside `logs/`; any traversal returns 422.
- Task commands run only through `TaskRunner`'s whitelist; no user-supplied string reaches a shell. `task_id` values are validated against `^\d{8}-\d{6}-[0-9a-f]{6}$` before use as a directory name.
- Repository URLs with embedded credentials (`https://user:pass@host/...`) are rejected outright — use the git connection instead.

### Site-to-bench API

When a site is created, the bench writes two keys into its `site_config.json`:

- `pilot_endpoint` — this bench's admin URL
- `pilot_auth_token` — a 365-day, site-scoped JWT (`scope: "site"`)

A site can call its own bench directly with these, no user login required:

```python
import frappe

endpoint = frappe.get_site_config().get("pilot_endpoint")
token = frappe.get_site_config().get("pilot_auth_token")
headers = {"Authorization": f"Bearer {token}"}

# Install an app on this site
requests.post(f"{endpoint}/api/v1/sites/{frappe.local.site}/apps",
              json={"app": "my-app"}, headers=headers)

# Enable TLS
requests.post(f"{endpoint}/api/v1/sites/{frappe.local.site}/actions/enable-tls",
              json={}, headers=headers)

# Set the backup schedule
requests.put(f"{endpoint}/api/v1/sites/{frappe.local.site}/backup-schedule",
             json={"schedule": "0 2 * * *"}, headers=headers)
```

The token is scoped: any attempt to reach a different site, or any bench-level route (`/benches`, `/settings`, ...), returns 403. Both keys are protected `site_config.json` fields — never exposed through `GET /sites/{name}/configuration` and preserved across config patches.

---

## Remote login via JWKS

Locally issued tokens are signed with the bench's own `admin.jwt_secret` (HS256) — only something that can read `bench.toml` can mint them. To let an **external control plane** authenticate without sharing that secret, set `admin.jwks_url` to a [JWKS](https://datatracker.ietf.org/doc/html/rfc7517) endpoint the issuer publishes:

```toml
[admin]
jwks_url = "https://control-plane.example.com/.well-known/jwks.json"
```

The issuer signs JWTs with a private key; the bench fetches the matching **public** keys from that URL and verifies incoming tokens against them — no shared secret, and a new bench inherits `jwks_url` from a sibling.

A JWKS-signed token has two uses:

- **Sign in to the admin UI** — hand it to the browser as `…/?sid=<jwt>`; the frontend exchanges it via `POST /api/v1/session` for the 1-day session cookie. It must be bench-scoped and carry a unique `jti` (makes it single-use; site-scoped tokens are refused here so they can't be escalated into a full admin session).
- **Drive the API directly** — send it as `Authorization: Bearer <jwt>` to any `/api/v1/*` route. Bearer tokens don't need a `jti`.

Scope claims work exactly as for local tokens: `scope: "site"` confines a token to its `site` claim (bearer use only); the bench-scoped default reaches every endpoint.

### JWKS endpoint format

`admin.jwks_url` must return a standard JSON Web Key Set: a document with a `keys` array. RSA and EC keys are both accepted:

```json
{
  "keys": [
    { "kty": "RSA", "use": "sig", "kid": "2026-07-rsa-1", "alg": "RS256", "n": "0vx7ag…", "e": "AQAB" },
    { "kty": "EC", "use": "sig", "kid": "2026-07-ec-1", "alg": "ES256", "crv": "P-256", "x": "f83OJ3D2…", "y": "x_FEzRu9…" }
  ]
}
```

| Key type | Required fields | Notes |
|----------|-----------------|-------|
| RSA (`kty: "RSA"`) | `n`, `e` | Modulus and exponent, base64url. `e` is almost always `"AQAB"`. |
| EC (`kty: "EC"`) | `crv`, `x`, `y` | Curve (`P-256`/`P-384`/`P-521`) and point coordinates, base64url. |

`kid` is recommended on every key, matched against the JWT header across rotations.

The signed JWT must use an asymmetric algorithm and carry an expiry:

- **Header:** e.g. `{"alg": "RS256", "typ": "JWT", "kid": "2026-07-rsa-1"}`. Accepted: `RS256/384/512`, `PS256/384/512`, `ES256/384/512`, `EdDSA`. Symmetric `HS*` and `none` are rejected.
- **Payload:** `exp` (Unix seconds) is required and enforced. `scope` (`"bench"` default, or `"site"` with a `site` claim); `jti` (required for `?sid=` sign-in); `aud` (required when `admin.jwks_audience` is set).

### Operational notes

- **Runs in the admin venv.** Verification uses [PyJWT](https://pyjwt.readthedocs.io/) with the `cryptography` backend (part of the `admin` extra); the `pilot` core stays dependency-free.
- **Caching & rotation.** The key set is cached for 5 minutes; an unknown `kid` never forces an immediate refetch. Publish a new key before signing with it, and keep the old one until its last token expires.
- **Fails closed.** An unreachable endpoint, malformed JWKS, unknown `kid`, disallowed algorithm, bad signature, or expired token all result in rejection (401/403), never a fallback to unauthenticated access.
- **HTTPS.** Serve the JWKS endpoint over HTTPS — its integrity is the root of trust for every remote login.

---

## CLI commands

- **`bench build-admin`** — builds the admin frontend static assets (`admin/frontend` → `admin/backend/static/dist`). Run after pulling admin UI changes.
- **`bench generate-admin-session`** — issues a one-time sign-in token (see Security notes).
- **`bench issue-site-token`** — issues a site-scoped bearer JWT.

Admin lifecycle is owned by `ProcessManager`: its `admin:` Procfile entry is written during `bench init`, and it starts/stops alongside every other bench process.
