# Configuration

Pilot stores configuration in two TOML files:

- `bench.toml` contains settings for one bench.
- `common_config.toml` sits beside the bench directories and contains settings shared by all benches on the host.

Use `BenchConfig` and Pilot's TOML store to read or write either file. Do not read `common_config.toml` directly.

## Quick start

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

The first app is the framework app. A bench uses one database type for all its sites. Supported types are `mariadb` and `postgres`.

## Bench settings

The `[bench]` table supports:

| Key | Purpose |
| --- | --- |
| `name` | Required bench name. |
| `python` | Required Python version. |
| `http_port` | Local web port. |
| `socketio_port` | Realtime port when processes are separate. |
| `socketio_backend` | `node` or `python`. |
| `db_type` | `mariadb` or `postgres`. |
| `default_branch` | Default branch for new apps. |
| `allow_developer_mode` | Allows per-site developer mode changes. |
| `watch_apps_js`, `watch_admin_js`, `reload_python` | Development reload settings. |
| `install_dev_extra` | Install apps' `dev` extras on a non-production bench. Defaults to true; `pilot init --no-dev` sets it to false. |

Applications use `[[apps]]` entries. `branches` can list branches available to the app:

```toml
[[apps]]
name = "erpnext"
repo = "https://github.com/frappe/erpnext"
branch = "version-15"
branches = ["version-15", "develop"]
```

`[redis]` defines separate cache and queue ports. `[[workers]]` defines queue groups and their process counts.

## Runtime modes

```toml
[production]
enabled = true
process_manager = "systemd" # or "supervisor"
```

Lite mode runs web, realtime, and jobs in one process:

```toml
[lite_mode]
enabled = true
restart_after_requests = 5000
restart_after_jobs = 500
restart_idle_seconds = 300
request_drain_seconds = 60
job_drain_seconds = 600
```

Request and job limits are counted from the last restart; `0` disables a limit. The process waits until it is idle before restarting. Drain settings bound graceful shutdown; a job that exceeds its limit is abandoned. Lite mode uses one worker group, so multiple groups are merged by taking the union of queues and the sum of their counts.

The process listens on `127.0.0.1:<http_port>` and serves realtime there too. Pilot updates `common_site_config.json` and nginx when lite mode changes. Frappe must provide `frappe/runner.py`; otherwise Pilot disables lite mode.

## Admin and sites

```toml
[admin]
enabled = true
port = 7000
domain = "admin.example.com"
tls = true
allow_bench_management = true
```

Nginx proxies admin traffic to `port + 1` on localhost. In production, `admin.domain` is required. `allow_bench_management` permits this admin to manage sibling benches and defaults to enabled only for development installs.

Set the admin password with `pilot set-admin-password` or the Settings page. Pilot stores a PBKDF2-HMAC-SHA256 verifier. `jwt_secret` is local; `jwks_url` and `jwks_audience` are for a remote token issuer and are host-shared. Pilot keeps the issuer's public keys in `.jwks-cache.json` in the benches directory. A token whose key is in the cache is verified without a fetch, and a cache older than one minute is refreshed in the background. A key that is not in the cache causes a fetch, at most once every 30 seconds. A failed fetch keeps the cached keys.

Site-specific settings, including developer mode, live in each site's `site_config.json`.

### Per-domain TLS

A site answers on its own name plus every entry in its `site_config.json` `domains` list. The `ssl` flag records whether clients use HTTPS for the site. A bare hostname follows that flag for both public and origin TLS. A route mapping decides public and origin TLS separately:

```json
{
  "ssl": false,
  "domains": [
    "www.example.com",
    {
      "domain": "shop.customer.com",
      "route": {
        "public_scheme": "https",
        "origin_scheme": "https",
        "client_ip_source": "proxy_protocol_v2"
      }
    }
  ]
}
```

Pilot creates one virtual host for each origin mode. A provider route can use HTTPS for clients and HTTP for its Pilot origin.

Pilot stores `ssl: true` and shows this route as TLS, but it does not get a certificate. A passthrough route uses HTTPS for both connections.

Pilot gets the passthrough certificate. Pilot also accepts the client IP source from the provider policy.

Pilot keeps the site route policy in `site_config.json`. Each attached domain can have a different route policy.

A domain the certificate does not name is served over HTTP rather than off a certificate that would fail to validate, so a half-finished `--expand` costs only that domain. Certificates are held in a certbot lineage named by the site's `cert_name`, defaulting to the site name; [renaming](commands.md#renaming-without-downtime) pins it so the certificate survives the site changing name.

## Other bench tables

These tables are per-bench unless noted otherwise:

- `[gunicorn]`: Gunicorn settings.
- `[build]`: manual override for the asset build memory cap.
- `[firewall]`: firewall behavior.
- `[waf]`: WAF rules and behavior.
- `[s3]`: backup storage credentials and bucket settings. Set `endpoint_url` for a custom S3-compatible provider.
- `[llm]`: admin assistant provider settings.
- `[resource_limits]`: CPU, memory, disk, uptime, and webhook alerts.

`[monitor]` contains this bench's application metric log path. Host-wide system, database, and slow-query logs always use `cli_root()/system/logs/` and cannot be configured.

Nginx uses compiled-in defaults; it has no `[nginx]` table in `bench.toml`.
Unknown fields are ignored during normal loads. Strict validation reports them.

## Shared host configuration

`common_config.toml` contains one copy of host-level settings:

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
enabled = false
bootstrapped = false

[[central.hostname_aliases]]
type = "site"
pattern = "site-*.par-1.frappe.cloud"
target = "site1.local"
redirect = true

[[central.hostname_aliases]]
type = "admin"
pattern = "vm-*.par-1.frappe.cloud"
target = "admin.local"
redirect = true

[telemetry]
endpoint = "https://telemetry-svc.par-1.frappe.cloud"
token = ""
logs_enabled = true
metrics_enabled = true

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

Shared tables are MariaDB, Postgres, Let's Encrypt, Central, the edge proxy, telemetry, resource limits, and the admin JWKS issuer. A bench exposes these values through its own `BenchConfig`; the model merges shared values on read and writes them back to the common file.

Central endpoint and authentication data come from instance metadata. While Central is enabled and bootstrap is pending, remote-token verification uses only the current staged JWKS URL, audience, and initial key set from instance metadata. Pilot caches that staged issuer in each Admin process until shared config changes or bootstrap completes. After bootstrap, it clears the staged entry and uses only the issuer saved in shared config. The metadata can include `initial_jwks_cache`, the issuer's JWK set. Pilot uses it to initialize an empty JWKS cache before it marks the host bootstrapped, so the first remote token does not wait for an issuer fetch. It does not replace keys already fetched from the issuer. `central.hostname_aliases` maps a VM hostname pattern to its current local target. The VM ID is assigned at runtime, so use `*` for that part. Pilot creates redirect rules only for aliases whose targets exist on the bench. Renaming a site or moving the admin domain re-points the matching alias automatically; remove one when the rule is no longer needed. `pilot setup central` writes these settings - see [Setup Commands](commands.md#setup-commands).

The domain provider controls the edge route for each hostname. Its route policy gives Pilot the public scheme, origin scheme, and client IP source.

Pilot configures PROXY protocol v2 when an HTTPS origin needs it. See [Per-domain TLS](#per-domain-tls).

### Telemetry

`[telemetry]` is one credential for both shippers. `endpoint` is the base URL of the region's Datum, with no path: metrics append `/v1/ingest` and logs append `/v1/logs/ingest`. `token` is the JWT Central mints for the region, which Datum tells apart by route rather than by token, so one serves both. `logs_enabled` and `metrics_enabled` switch each shipper independently.

Either shipper runs only when its switch is on and both `endpoint` and `token` are set. Metrics also need the optional `datum` package (`pip install pilot[metrics]`). Logs are written locally regardless of shipping settings.

`pilot setup telemetry` writes this section and installs the log shipper. Central is asked for the credential when `[central] enabled` is set, and what it returns replaces what is already there, because the token expires. `--endpoint` and `--token` override the fetch. `pilot setup production` does the same, once, before configuring either shipper.

### Alert Delivery

`[resource_limits]` sets when an alert is raised and where it goes. A usage percentage of `0` disables that alert; `site_uptime` covers sites that stop answering their ping. A condition must hold for five minutes before anything is sent.

Where an alert goes depends on what is configured. A Central-managed host reports every alert to Central; a self-hosted one does not, and the dashboard drops the Central wording along with it. Each entry in `webhook_endpoints` receives alerts either way, as a POST with an `Authorization: Bearer` header. Mail is a third sink, off until a mail server is configured and `email_recipients` is set.

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

## Credentials and URLs

Database passwords are passed through environment variables, not command-line arguments. MariaDB site setup uses a temporary, site-scoped account; Postgres setup requires the configured superuser.

`admin.jwks_url`, `telemetry.endpoint`, and `llm.api_base` must use `http` or `https`, contain no credentials, and must not target link-local or cloud metadata hosts. Loopback and private addresses are allowed. Validation checks the hostname itself, not where DNS resolves it.

After upgrading an old bench, `merge_common_config` moves host-wide fields from `bench.toml` into `common_config.toml`. Run `pilot admin run-patches` if needed.
