# Configuration

`bench.toml` is the source of truth for a bench. Read and write it through the config model and TOML store.

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

`[mariadb]` and `[postgres]` describe how the bench connects to the chosen engine. `existing = true` means the user supplied the service and Pilot should not infer or manage it as owned state.

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
use_companion_manager = false
```

Supported process managers are `systemd` and `supervisor`.

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

JWT fields are:

- `jwt_secret`: local token signing secret.
- `jwks_url`: remote issuer key set.
- `jwks_audience`: required when `jwks_url` is used.

## Other Groups

- `[monitor]`: monitoring settings.
- `[nginx]`: nginx rendering settings.
- `[gunicorn]`: Gunicorn process settings.
- `[letsencrypt]`: certificate settings.
- `[central]`: Central endpoint and Pilot auth token.
- `[firewall]`: firewall behavior.
- `[waf]`: WAF behavior.
- `[s3]`: S3 backup credentials and bucket settings.

Unknown fields are ignored by normal loads for compatibility. Strict validation can report unknown config paths.
