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
| `onboarding` | State of the onboarding dialog: empty shows it, `minimized` shows in sidebar card only, `dismissed` hides both |

Applications use `[[apps]]` entries. `branches` can list branches available to the app:

```toml
[[apps]]
name = "erpnext"
repo = "https://github.com/frappe/erpnext"
branch = "version-15"
branches = ["version-15", "develop"]
```

An app that the marketplace registry lists updates to the registry's newest release for its branch. It updates to the branch tip instead when the registry has no release for that branch, or when the entry sets `follow_branch = true`, for example a developer who pushes to the app's own repository:

```toml
[[apps]]
name = "myapp"
repo = "https://github.com/frappe/myapp"
branch = "develop"
follow_branch = true
```

A registry release is offered only when it is newer than the installed commit. A full clone checks that the release descends from it. A shallow clone fetches only the release commit, without the history between them, so a newer version decides, and between equal versions, such as `17.0.0-dev` on `develop`, a later commit date decides.

`[redis]` defines separate cache and queue ports. Pilot writes them to `config/redis_cache.conf` and `config/redis_queue.conf` inside a `PILOT MANAGED BLOCK` and keeps the lines outside it, such as `loadmodule /path/to/redisearch.so`. `[[workers]]` defines queue groups and their process counts.

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

Nginx proxies admin traffic to `port + 1` on localhost. In production, `admin.domain` is required. `allow_bench_management` permits this admin to manage sibling benches and defaults to enabled only for development installs. It also permits killing database processes and purging binlogs, because both affect every bench on the host. Database settings and the MariaDB restart do not need it.

A new site calls Pilot at `pilot_endpoint` in its `site_config.json`. This is the admin URL on localhost, so it does not depend on DNS or `admin.domain`. The browser loads Cloud Settings from `cloud_server_url` and `cloud_settings_embed_url`, which hold the public admin URL. An admin domain change updates the public URLs and any `pilot_endpoint` that is not on localhost.

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
- `[processes]`: `disabled`, the app processes that stay off. `pilot processes disable` and `enable` write it. See [App Processes](app-processes.md).
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
memory_share = 0.5

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

[proxy]
protocol_v2 = false
cloudflare = false

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

[frappe_cloud]
url = "https://cloud.frappe.io"
```

Shared tables are MariaDB, Postgres, Let's Encrypt, Central, the edge proxy, telemetry, resource limits, Frappe Cloud, and the admin JWKS issuer. A bench exposes these values through its own `BenchConfig`; the model merges shared values on read and writes them back to the common file.

Central endpoint and authentication data come from instance metadata. While Central is enabled and bootstrap is pending, remote-token verification uses only the current staged JWKS URL, audience, and initial key set from instance metadata. Pilot caches that staged issuer in each Admin process until shared config changes or bootstrap completes. After bootstrap, it clears the staged entry and uses only the issuer saved in shared config. The metadata can include `initial_jwks_cache`, the issuer's JWK set. Pilot uses it to initialize an empty JWKS cache before it marks the host bootstrapped, so the first remote token does not wait for an issuer fetch. It does not replace keys already fetched from the issuer. The Central configuration is in the `pilot-central` attribute. Three optional attributes hold [JSON Merge Patches](https://www.rfc-editor.org/rfc/rfc7396) for config files: `pilot-common-config` for `common_config.toml`, `pilot-config` for `bench.toml`, and `pilot-common-site-config` for `sites/common_site_config.json`. For example, `pilot-config` can hold `{"s3": {"bucket": "team-backups"}}`. A patch changes only the keys it names, and `null` resets a key to its default. `pilot-common-config` must not change `central`, because bootstrap owns that table. Pilot rejects unknown TOML keys and validates the result before it writes a file. Pilot applies the patches once, during bootstrap, before it marks the host bootstrapped, and does not read them again. Central must write them before `pilot-central`. Each file has its own attribute because the cloud caps a metadata value at 1 KiB. `central.hostname_aliases` maps a VM hostname pattern to its current local target. The VM ID is assigned at runtime, so use `*` for that part. A pattern such as `*.par-1.frappe.cloud` matches every name in the zone. When patterns overlap, the pattern with the most literal text wins, so `admin-vm-*.par-1.frappe.cloud` takes precedence over it, and a site's own domains take precedence over every pattern. Pilot creates redirect rules only for aliases whose targets exist on the bench. A redirecting site alias sends clients to the site's primary domain, on the scheme clients reach it with, but still answers `/api/method/ping`, because Central probes the alias for readiness without following redirects. Renaming a site or moving the admin domain re-points the matching alias automatically; remove one when the rule is no longer needed. `pilot setup central` writes these settings - see [Setup Commands](commands.md#setup-commands).

Before it applies the Central credential, bootstrap sizes Pilot's MariaDB for the VM's memory, because an image built on another host keeps that host's sizing. It writes `my.cnf` and the unit for the next start, then applies the values to the running server without a restart: the buffer pool and its automatic minimum, `innodb_log_file_size`, `key_buffer_size`, and `max_connections`. An option set in `managed.cnf` keeps its value. The buffer pool cannot grow above the running `innodb_buffer_pool_size_max`, so a larger pool waits for the next start. Higher unit memory limits apply before MariaDB grows. Lower limits apply only after MariaDB uses less than the new `MemoryHigh`. The unit file is written last, after the limits apply; if the usage stays higher or cannot be read for a minute, the old unit stays and a later attempt tries again.

The user unit `pilot-mariadb-sizing.service` prepares MariaDB before its first start in each host boot and stays active for that boot. Pilot records the boot ID and total host RAM in `memory-sizing.json`. On the first allocation, or a new boot with changed RAM, it recalculates the generated settings and clears custom buffer-pool sizes, log-file size, key-buffer size, connection count, and service memory limits. A MariaDB restart or a host reboot with unchanged RAM preserves custom values. Other database-variable overrides, including `innodb_snapshot_isolation`, remain unchanged. The upgrade patch records the current host capacity and starts the sizing service outside the database action lock, so installation preserves existing settings and the first MariaDB restart can proceed.

Settings, Database, Database Configurations exposes `MemoryHigh` and `MemoryMax` in MiB for the shared managed Linux service. Optional `mariadb.memory_high_mb` and `mariadb.memory_max_mb` in `common_config.toml` persist manual limits across restarts. Both must be positive integers, with `MemoryHigh` below `MemoryMax`. The hard limit must exceed current MariaDB use and the configured buffer pool; lowering the soft limit requires it to remain above current use. At submission and execution, Pilot rejects a hard limit that, combined with estimated memory use by other host processes, exceeds 90% of total RAM. Unavailable MariaDB usage blocks the change. This check reflects current usage and does not reserve capacity against later growth. A host RAM change clears both saved limits and applies automatic values using `mariadb.memory_share`. Swap is not editable here.

`innodb_snapshot_isolation` is editable in Database Configurations. Its warning explains that enabling it can reject conflicting updates or deletes and roll back transactions; disabling it removes that conflict protection. The change affects new connections to the shared database. Existing connections retain their session setting. The custom value is saved in `managed.cnf` and survives host RAM changes.

The buffer-pool editor also caps its maximum below the effective MariaDB `MemoryMax`, in addition to the host-based range. The setter reads the hard limit again under the database action lock before writing settings or changing the pool, so a queued resize cannot exceed a newly lowered limit.

On a host with less than 800 MB of memory, MariaDB gets a 64 MB buffer pool, an 8 MB key buffer, and 20 connections, which is enough for one bench with one site. `MemoryHigh` then stays at least a quarter of `MemoryMax` below it.

`mariadb.memory_share` is the share of host memory that Pilot's MariaDB may use, from 0.2 to 0.8 (default 0.5); the benches keep the rest. Settings, Database, Memory split changes it for every bench of the host and re-sizes the running server. Selecting a split clears manual service-limit overrides. When the new limits apply, Pilot saves them in both the main unit and its memory drop-in so they survive a reboot with unchanged RAM. A deferred shrink keeps the previous saved limits until it can apply safely. A larger buffer pool than the running server allows takes effect at the next MariaDB start.

The domain provider controls the edge route for each hostname. Its route policy gives Pilot the public scheme, origin scheme, and client IP source.

Pilot configures PROXY protocol v2 when an HTTPS origin needs it. See [Per-domain TLS](#per-domain-tls).

Set `proxy.cloudflare = true` when site domains use Cloudflare's proxy. nginx then takes the client IP from the `CF-Connecting-IP` header, but only from [Cloudflare's addresses](https://www.cloudflare.com/ips/). A domain that resolves to Cloudflare's addresses passes the add-domain DNS check with a warning, because Pilot cannot see the server behind the proxy. This check does not need the setting. In Cloudflare, use the Full (strict) SSL mode. In Flexible mode, Cloudflare sends requests to Pilot over HTTP, Pilot redirects them to HTTPS, and the browser loops. Let's Encrypt checks the domain over HTTP, so turn off Always Use HTTPS in Cloudflare until Pilot issues the certificate.

### Frappe Cloud

`[frappe_cloud] url` is the Frappe Cloud that a site restore copies backups from. The default is `https://cloud.frappe.io`. Set it to use another Frappe Cloud, for example a local Press during development.

### Scheduled Backups

Each bench has one hourly `0 * * * *` cron job for scheduled backups. It is installed with the first enrolled site. Sites without a custom backup schedule use this default. New sites are enrolled, including sites created from a backup, and the `enroll_sites_in_scheduled_backups` patch enrolls existing sites that have no backup schedule. Each run reads the last 24 hours of the bench audit log once, then queues a logical backup of the database and site config for each enrolled site with no successful backup in the last 23 hours. Measuring 23 hours keeps each site's backup at the same hour every day. A backup also includes public and private files when the site has no file backup that day (UTC). Physical backups are not scheduled. After six failed backups in 24 hours, the job skips the site until the count drops. The sixth failure since the last successful backup adds a "Backups of <site> are failing" notification to the bench feed. Without S3, scheduled backups stay on disk in `sites/<site>/backups`, outside the directory that Frappe prunes. When S3 is configured, every backup streams straight into S3 and writes nothing to disk: Pilot points `frappe backup` at named pipes and uploads what it reads from them as multipart uploads, and a run that fails partway leaves no files in S3. A site with backup encryption turned on in System Settings, or whose setting cannot be read, is backed up to disk and then uploaded instead, because Frappe encrypts in a second pass over the finished files. Expired runs are deleted from S3. The host must have cron and the Pilot task worker running. A scheduled backup does not start while another task holds the site.

After each backup, Pilot clears expired runs with the default GFS policy: every run is kept for 7 days, Sunday runs for 28 days, runs on the first day of a month for 366 days, and January 1 runs for 3,653 days. The newest run is always kept. The `backup_retention` object in `site_config.json` marks this policy with `scheme: "gfs"` and `calendar_rotation: true`. A custom schedule set in the dashboard or through the backup schedule API replaces the default. It installs a cron job for the site and stores a GFS or FIFO retention record without `calendar_rotation`, so the bench job skips the site and the count-based policy clears its runs. Turning off automated backups removes the schedule and the retention record, and the site then keeps only manual backups.

### Telemetry

`[telemetry]` is one credential for both shippers. `endpoint` is the base URL of the region's Datum, with no path: metrics append `/v1/ingest` and logs append `/v1/logs/ingest`. `token` is the JWT Central mints for the region, which Datum tells apart by route rather than by token, so one serves both. `logs_enabled` and `metrics_enabled` switch each shipper independently.

Either shipper runs only when its switch is on and both `endpoint` and `token` are set. Logs are written locally regardless of shipping settings.

`pilot setup telemetry` writes this section and installs the log shipper. Central is asked for the credential when `[central] enabled` is set, and what it returns replaces what is already there, because the token expires. `--endpoint` and `--token` override the fetch. `pilot setup production` does the same, once, before configuring either shipper.

The monitor checks this section on every run, so both shippers start when a credential arrives later, such as from Central's bootstrap. It rewrites the Fluent Bit config and restarts Fluent Bit when the endpoint or token changes, and it starts Fluent Bit again when it is not running. Fluent Bit must be installed. `install.sh` installs it on Debian and Ubuntu. `pilot remove logs` stops Fluent Bit and sets `logs_enabled = false`, so the monitor does not start it again.

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
- `use_tls` upgrades the connection with STARTTLS
- `use_ssl` connects over SSL instead, with `use_tls` set to `0`. The framework reads `use_ssl` from Frappe v16 with frappe/frappe#44142.
- `mail_port` may be left at `0`, which means 465 with SSL and 587 with STARTTLS
- `auto_email_id` is the address alerts are sent from, and the login name by default
- `mail_login` only when the server expects a login name that is not that address
- a relay that takes no credentials gets `disable_mail_smtp_authentication` instead of a password

Each site's `site_config.json` has `email_sender_name`, the display name on mail that the site sends through this mailbox without a sender name of its own, such as a password reset. Mail that a user sends keeps that user's name. A restore keeps the target site's value. Pilot sets it from the first label of the site name when it creates the site, and changes it when the site is renamed. It changes `-` and `_` to spaces and starts each word with a capital letter, such as `Acme Corp` for `acme-corp.example.com`. A rename keeps a value that you set yourself.

The certificate is verified in both modes, so a server with a self-signed certificate is refused rather than trusted silently.

Saving these through the Admin UI opens a session against the server first, so a wrong password or an unreachable host is reported then rather than at the first alert. The Admin API never returns the password, reporting only whether one is stored.

`email_recipients` is edited on the notification settings page, the mailbox on its own one, so either may be saved before the other exists.

## Credentials and URLs

Database passwords are passed through environment variables, not command-line arguments. MariaDB site setup uses a temporary, site-scoped account; Postgres setup requires the configured superuser.

`admin.jwks_url`, `telemetry.endpoint`, and `llm.api_base` must use `http` or `https`, contain no credentials, and must not target link-local or cloud metadata hosts. Loopback and private addresses are allowed. Validation checks the hostname itself, not where DNS resolves it.

After upgrading an old bench, `merge_common_config` moves host-wide fields from `bench.toml` into `common_config.toml`. Run `pilot admin run-patches` if needed.
