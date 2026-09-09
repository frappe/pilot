# Configuration

`bench.toml` is the source of truth for a bench. Read and write it through the config model and TOML store. `BenchConfig` is also the sole reader/writer of `common_config.toml`, the host-shared file described in [Common Config](#common-config).

## Minimal Example

```toml
[bench]
name = "main"
python = "3.11"
http_port = 8000
socketio_port = 9000
socketio_backend = "node"
db_type = "mariadb"

[[apps]]
name = "frappe"
repo = "https://github.com/frappe/frappe"
branch = "version-15"

[redis]
cache_port = 13000
queue_port = 11000

[[workers]]
queues = ["default", "short", "long"]
count = 1
```

## `[bench]`

- `name`: required bench name.
- `python`: required Python version.
- `http_port`: web port for local runtime.
- `socketio_port`: websocket port.
- `socketio_backend`: `node` or `python`.
- `db_type`: `mariadb`, `postgres`, or `sqlite`.
- `default_branch`: optional branch default for new apps.
- `allow_developer_mode`: allows developer mode to be toggled per site. Developer mode itself stays in each site's `site_config.json`.
- `watch_apps_js`, `watch_admin_js`, `reload_python`: development toggles.

## Apps

Each `[[apps]]` entry records one app:

```toml
[[apps]]
name = "erpnext"
repo = "https://github.com/frappe/erpnext"
branch = "version-15"
branches = ["version-15", "develop"]
```

The first app is treated as the framework app when code needs that distinction.

## Databases

`config.mariadb` and `config.postgres` describe how a bench connects to the chosen engine. `existing = true` means the user supplied the service and Pilot should not infer or manage it as owned state. Both live in `common_config.toml`, not `bench.toml` - see [Common Config](#common-config).

One bench uses one database engine for its sites. Pick it with `bench.db_type`.

## Redis And Workers

`[redis]` has separate cache and queue ports. They must be distinct.

Workers use `[[workers]]` array entries:

```toml
[[workers]]
queues = ["default", "short", "long"]
count = 2
```

## Production

```toml
[production]
enabled = true
process_manager = "systemd"
```

Supported process managers are `systemd` and `supervisor`.

## Lite Mode

Lite mode runs the whole bench as one process - web, realtime and background jobs
together - instead of separate web, socketio and worker processes. Every key below
becomes a flag on `python -m frappe.runner`, which is what the `web` process runs.
The process manager still supervises it; only the process set changes.

A new bench is created with `enabled = true`. A bench.toml written before this was
the default carries no `[lite_mode]` table, and keeps the separate process set until
you turn lite mode on in Settings. Turning it off writes `enabled = false`.

```toml
[lite_mode]
enabled = true
restart_after_requests = 5000
restart_after_jobs = 500
restart_idle_seconds = 300
request_drain_seconds = 60
job_drain_seconds = 600
```

The process recycles itself to release heap. `restart_after_requests` and
`restart_after_jobs` are counted since the last restart, and `0` disables either
limit. Reaching a limit only books the restart: it happens once no web request
has been served for `restart_idle_seconds`, so it never lands mid-traffic. Only
2xx responses count, and realtime polls and health checks are ignored - a browser
tab and a monitor ping forever, and a process counting those would never look idle.

`request_drain_seconds` and `job_drain_seconds` bound the graceful shutdown on
every restart and stop. A job still running when its drain expires is abandoned,
so keep `job_drain_seconds` above your longest job.

Lite mode uses one worker pool. Thus `[[workers]]` holds one record. Its queues become
`--queue`, and its `count` becomes `--job-threads`. One pool cannot give a different
count to each set of queues. Thus pilot folds more records into one record. The queues
of the new record are the union of the queues. Its count is the total of the counts.
The audit log records this as `worker_groups_collapsed`. The Workers tab shows the one
record, and it does not show an Add button.

The process listens on `127.0.0.1:<bench.http_port>`. This is the address that gunicorn
uses when lite mode is off. The process also serves realtime on this port. Thus pilot
writes this port as `socketio_port` in `common_site_config.json`, and nginx sends
`/socket.io` to it.

One process holds one client cache. Thus pilot writes `client_cache_max_bytes` as
10 MB in `common_site_config.json`, and removes the key when you turn lite mode off.

The process needs the `uvicorn` and `a2wsgi` packages. Frappe declares them, but an
environment from before frappe added them does not have them. Run
`pilot -b <bench> setup requirements` after you update frappe.

Only a frappe that has `frappe/runner.py` can run lite mode. Without this file, the
Settings page does not show the switch, and the bench runs the usual process set. The
next save of the settings sets `enabled` to false and records `lite_mode_disabled` in
the audit log.

A change to `enabled` starts a `switch-lite-mode` task. The task stops the workload. It
writes `common_site_config.json` and the nginx configuration again. It installs the
units again, and removes the units that the new mode does not use. Then it starts the
workload again. The task does not stop admin, because the admin unit is the same in the
two modes.

## Admin

```toml
[admin]
enabled = true
port = 7000
domain = "admin.example.com"
tls = true
allow_bench_management = true
```

`admin.internal_port` is derived as `port + 1` for the localhost Gunicorn service behind nginx.

`allow_bench_management` gates creating and managing sibling benches from this bench's Admin. It defaults to `true` only on a development checkout (`install.sh --dev`). A release install defaults to `false`, so set it in `bench.toml` to turn it on.

`password` is stored as a PBKDF2-HMAC-SHA256 hash (`$pbkdf2-sha256$<iterations>$<salt>$<key>`, hashlib only - no dependency), so `bench.toml` holds a verifier rather than the password. Set it with `pilot set-admin-password` or the Settings page; a bench upgraded from an older version is migrated by the `hash_admin_password` patch, and its cleartext keeps working until then.

`jwt_secret` is this bench's own local token signing secret, kept in `bench.toml`. `jwks_url` and `jwks_audience` trust a remote issuer instead and are host-shared - see [Common Config](#common-config).

## Other Groups

- `[monitor]`: per-bench `log_path` for this bench's own application metrics. The host-wide system/DB/slow-query log paths are fixed at `cli_root()/system/logs/*` and not configurable anywhere.
- `[gunicorn]`: Gunicorn process settings.
- `[firewall]`: firewall behavior.
- `[waf]`: WAF behavior.
- `[s3]`: S3 backup credentials and bucket settings.
- `[llm]`: LLM provider settings used by the admin assistant.

Nginx has no per-bench `bench.toml` section - `config.nginx` always holds its compiled-in defaults (ports 80/443, platform-default `config_dir`, etc.); nothing in `bench.toml` can override it.

Unknown fields are ignored by normal loads for compatibility. Strict validation can report unknown config paths.

## Database Credentials

`mariadb.root_password` and `postgres.root_password` never reach a command line. Pilot's own client calls pass them through `MYSQL_PWD`/`PGPASSWORD`, and the frappe commands that set a site up (`new-site`, `restore`, `reinstall`, `drop-site`) get a throwaway MariaDB account instead: `MariaDBManager.temporary_setup_user` grants it `RELOAD`, `CREATE USER`, and full rights on that one site database, then drops it when the command returns. Pilot therefore names the site database itself (`_<16 hex>`) rather than letting frappe pick a random one. Postgres still passes the superuser credential, because frappe's Postgres setup needs privileges a scoped role cannot hold.

## Fetched Endpoints

`admin.jwks_url`, `central.endpoint`, `datum.endpoint`, and `llm.api_base` are URLs this bench requests itself, so `BenchConfig.validate` sends each through `validate_external_url`: the scheme must be `http` or `https`, credentials must not be embedded, and the host must not be link-local or a cloud metadata name. Loopback and private addresses stay allowed - a self-hosted model or a local JWKS issuer is a normal setup. Validation reads the literal host only; a domain that resolves to a blocked address is not caught.

## Common Config

Some settings are shared by every bench under one benches directory, not owned by any single bench: one MariaDB server, one Postgres server, one ACME account, one trusted admin JWKS issuer, one Central enrolment, one metrics destination. These live in `common_config.toml`, next to the bench folders, not in any bench's own `bench.toml`:

```toml
[mariadb]
host = "localhost"
port = 3306
admin_user = "root"
root_password = ""
socket_path = ""
existing = false

[postgres]
host = "localhost"
port = 5432
admin_user = "postgres"
root_password = ""
existing = false

[letsencrypt]
email = "ops@example.com"
webroot_path = "/var/www/letsencrypt"

[central]
endpoint = "https://central.example.com"
auth_token = ""

[datum]
endpoint = "https://datum.internal"
token = ""

[admin]
jwks_url = "https://issuer.example.com/jwks.json"
jwks_audience = "bench-fleet"

[resource_limits]
cpu_usage_limit = 85
memory_usage_limit = 90
disk_space_limit = 0
site_uptime = true
webhook_endpoints = { "https://alerts.example.com/pilot" = "bearer-token" }
email_recipients = ["ops@example.com"]
```

### Alert Delivery

`[resource_limits]` sets when an alert is raised and where it goes. A usage percentage of `0` disables that alert; `site_uptime` covers sites that stop answering their ping. A condition must hold for five minutes before anything is sent.

Alerts always go to Central. Each entry in `webhook_endpoints` receives them too, as a POST with an `Authorization: Bearer` header. Mail is a third sink, off until a mail server is configured and `email_recipients` is set.

Outgoing mail lives in the bench's `sites/common_site_config.json`, under the keys the framework's Email Account already reads, so a site picks the same mailbox up without any further setup:

```json
{
  "mail_server": "smtp.example.com",
  "mail_port": 587,
  "auto_email_id": "alerts@example.com",
  "mail_login": "alerts@example.com",
  "mail_password": "secret",
  "use_tls": 1
}
```

- `mail_server` is the outgoing mail server, `mail_port` the port it listens on
- `use_tls` upgrades the connection with STARTTLS; `0` connects over SSL instead
- `mail_port` may be left at `0`, which means 465 with SSL and 587 with STARTTLS
- `auto_email_id` is the address alerts are sent from, and the login name by default
- `mail_login` only when the server expects a login name that is not that address
- a relay that takes no credentials gets `disable_mail_smtp_authentication` instead of a password

The certificate is verified in both modes, so a server with a self-signed certificate is refused rather than trusted silently.

Saving these through the Admin UI opens a session against the server first, so a wrong password or an unreachable host is reported then rather than at the first alert. The Admin API never returns the password, reporting only whether one is stored.

`email_recipients` is edited on the notification settings page, the mailbox on its own one, so either may be saved before the other exists.

`BenchConfig` is the only reader/writer of this file - it merges these values into `config.mariadb`, `config.postgres`, `config.letsencrypt`, `config.central`, `config.datum`, and `config.admin.jwks_url`/`jwks_audience` on every read, and writes them back on save. Other code reaches these values through a bench's own `BenchConfig`, never by reading `common_config.toml` directly. `admin.tls` is not part of this file - it stays a per-bench choice in `bench.toml`.

`[datum]` is where the monitor ships metrics. With both `endpoint` and `token` set, and the optional `datum` package installed (`pip install pilot[metrics]`), every collection tick is posted as one batch of samples. The JSON-Lines monitor logs are written either way - they stay the Admin UI's source of truth.

The host-wide system/DB/slow-query monitor log paths (`system_log_path`/`db_log_path`/`slow_query_log_path`) are not configurable at all, in `bench.toml` or `common_config.toml` - they're fixed at `cli_root()/system/logs/{system-stats.log,db-stats.log,slow-queries.json}` (see `pilot/config/monitor.py`).

A pre-upgrade bench whose `bench.toml` still carries these fields directly is migrated by the `merge_common_config` patch - see [pilot/patches](../pilot/patches) and `pilot admin run-patches`.
