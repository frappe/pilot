# Production Setup Specification

Covers DNS-based multitenancy, Nginx reverse-proxy configuration, and Let's Encrypt SSL certificate management. All settings live in `bench.toml`.

> **Platform scope:** Everything in this file targets **Ubuntu/Linux servers**. Let's Encrypt requires a publicly reachable server with real DNS records. Nginx system integration (`/etc/nginx/conf.d/`, `systemctl reload nginx`) is Linux-specific. On macOS, use `bench run` with honcho for development; skip the `bench setup` commands entirely.

---

## Overview

A production bench differs from a development bench in three ways:

1. **Process manager** — processes run as a background daemon managed by supervisor (default) or systemd.
2. **Nginx sits in front of Gunicorn** — terminates SSL, serves static assets directly, and proxies dynamic requests to a pool of Gunicorn workers. Gunicorn runs the Frappe WSGI application with multiple workers.
3. **Each site has a real domain** — Frappe uses the `Host` header to route requests to the correct site database and files. This is called DNS multitenancy.

The `bench setup` sub-commands orchestrate these concerns:

| Command | What it does |
|---------|-------------|
| `bench setup production` | Configure process manager, nginx, and SSL in the correct order |
| `bench setup nginx` | Generate per-site Nginx config files and install them |
| `bench setup letsencrypt` | Obtain Let's Encrypt certificates for all SSL-enabled sites and the admin domain |
| `bench restart` | Restart all bench processes via supervisor or systemd |

---

## DNS multitenancy

Frappe identifies the current site by reading the HTTP `Host` header on every request. No per-process site assignment is needed — all web workers serve all sites from a single Gunicorn pool.

To enable this, `bench setup production` writes `"dns_multitenant": 1` into `sites/common_site_config.json`. Nginx passes the original host to Frappe via two headers:

```nginx
proxy_set_header Host              $host;
proxy_set_header X-Frappe-Site-Name $host;
```

A site whose `name` (or any entry in its `domains` list) matches the incoming `Host` header is served. If no match is found, Frappe returns a 404.

### Site name vs domains

The site `name` in `bench.toml` is the canonical hostname — it is the directory name under `sites/` and the key Frappe uses internally. Each site may also declare additional `domains` that are aliases pointing at the same site. Nginx includes all of them in the `server_name` directive and in the SSL certificate SAN list.

```
site1.example.com    ← site name (canonical)
www.site1.example.com ← domain alias
```

---

## Custom domain management

Beyond the `domains` baked into `site_config.json` above, a site's domains can be managed at runtime — attaching a domain a customer brings, verifying it actually points here, and choosing which one is primary. This backs the admin UI's **Domains** tab on a site, via `DomainRouteProvider` (see `#### DomainRouteProvider` under Architecture additions).

**Attach flow (two steps):**

1. **Get DNS records** — for a candidate domain, validate it isn't already on this site, another site in this bench, this bench's admin domain, or a sibling bench (`host_owner`), then return CNAME (to the site name) and A (to this server's public IP) options to show the user.
2. **Register** — re-run the same checks, verify the domain actually resolves to this server (CNAME to the site, or a matching A record), then append it to `site_config.json`'s `domains`. A domain that doesn't resolve, or points elsewhere, is rejected and the operator is told to retry once DNS has updated.

Detaching just removes the domain from the list; the current primary (`host_name`) can't be removed until another domain is made primary first.

### Wildcard domains

A bench can offer wildcard domain patterns (e.g. `*.example.com`, or a per-host suffix like `*-box1.example.com`) that new site names and the admin domain must match. Matching strips the leading `*` from the pattern and requires the candidate to end with that suffix and have something before it. When wildcard domains are configured, `bench new-site` and `bench setup production --admin-domain` reject any name that doesn't match one of them; the admin UI's Create Site and New Bench dialogs build the name from a typed prefix plus the wildcard suffix (a fixed label with one pattern, a dropdown with several). With none configured, both fall back to a plain free-text field.

### `bench-domain-provider` extension

All of the above is this bench's own implementation. If a `bench-domain-provider` executable is found on `PATH` (e.g. installed by a managed/cloud hosting platform), it takes over entirely instead. See [domain-provider.md](domain-provider.md) for the CLI contract it must implement.

---

## bench.toml additions

### `production` section (required for production mode)

`bench setup production` writes the `[production]` section; `bench remove production` clears it. nginx is always part of a production deploy — there is no separate nginx flag.

```toml
[production]
enabled = true                   # set by bench setup production
process_manager = "supervisor"   # systemd | supervisor | openrc
use_companion_manager = false    # run scheduler/workers/socketio inside gunicorn
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | bool | no | `false` | `true` once deployed to production. Gates `bench restart`, nginx setup, and production process management. |
| `process_manager` | string | no | `""` | Production process manager: `systemd`, `supervisor`, or `openrc` (Alpine). Empty on an undeployed bench. |
| `use_companion_manager` | bool | no | `false` | Run scheduler, RQ workers, and socket.io as Gunicorn companion processes under a single preloaded master. Requires the Frappe Gunicorn fork with companion support. |

**Supervisor** (`process_manager = "supervisor"`, default):
- Installs `supervisor` package if not present and disables the system-wide service.
- Writes `config/supervisor/supervisord.conf` and starts a bench-owned supervisord.
- No `sudo` needed for day-to-day process management.

**Systemd** (`process_manager = "systemd"`):
- Writes `.service` files to `~/.config/systemd/user/` and a `.target` that groups them.
- Requires `sudo loginctl enable-linger <user>` once so the user session persists after logout.
- No `sudo` needed after that — `systemctl --user` manages everything.

**OpenRC** (`process_manager = "openrc"`, the Alpine default):
- The Alpine counterpart of systemd. Writes one `supervise-daemon` init script per process to `config/openrc/`, symlinks them into `/etc/init.d/`, and enables them with `rc-update`.
- `supervise-daemon` keeps each process alive (the equivalent of systemd's `Restart=on-failure`).
- The admin runs as a plain supervised Flask process on `admin.port` (no socket activation); nginx proxies straight to it. The workload and admin are separate services, so `bench stop` stops the workload while the admin keeps serving.
- Selected automatically on Alpine; per-bench `mariadb-<instance>` OpenRC services are provisioned (no `mariadb@.service` template units needed).

### Companion manager

When `use_companion_manager = true`, the generated `config/gunicorn.conf.py` includes `companion_workers` for a single RQ worker-pool and socket.io. Gunicorn forks a companion manager that supervises these processes, so they share the preloaded application memory copy-on-write.

The worker-pool runs every queue with `num_workers` equal to the total worker count across all `[workers]` groups (queues are the deduped union of every group's queues). The Frappe scheduler runs as a thread inside the pool workers, so it needs no companion of its own — one fewer process than the legacy per-worker layout.

In this mode the supervisor/systemd configs only manage the Gunicorn web process (plus admin and Redis). The web service gets an extended stop timeout (1600s) so Gunicorn has time to drain all companions before SIGKILL.

### `sites[]` — new optional fields

```toml
[[sites]]
name = "site1.example.com"
db_name = "site1_db"
db_password = "secret"
apps = ["frappe", "erpnext"]
domains = ["www.site1.example.com"]  # additional hostnames served by this site
ssl = true                            # obtain a Let's Encrypt cert covering name + domains
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `domains` | list of strings | no | `[]` | Extra hostnames that should resolve to this site. Nginx includes them in `server_name`; certbot includes them as SANs. |
| `ssl` | bool | no | `false` | When `true`, the Nginx config terminates TLS and redirects HTTP to HTTPS. Requires a cert obtained via `bench setup letsencrypt`. |

### `nginx` section

nginx is mandatory for production and has no enable flag of its own — production is gated by `production.enabled` (set by `bench setup production`).

```toml
[nginx]
http_port = 80
https_port = 443
config_dir = "/etc/nginx/conf.d"   # where to write the include-pointer file (requires sudo)
worker_processes = "auto"          # passed through to nginx.conf
client_max_body_size = "50m"       # for file uploads
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `enabled` | bool | yes | `false` | Gate that prevents accidental nginx setup in development. |
| `http_port` | int | no | `80` | Port Nginx listens on for HTTP. |
| `https_port` | int | no | `443` | Port Nginx listens on for HTTPS. |
| `config_dir` | string | no | `/etc/nginx/conf.d` | System directory where the bench include-pointer file is written. |
| `worker_processes` | string\|int | no | `auto` | Passed directly to the Nginx `worker_processes` directive. |
| `client_max_body_size` | string | no | `50m` | Maximum request body size; increase for large file uploads. |

### `gunicorn` section (new)

```toml
[gunicorn]
workers = 4                          # number of Gunicorn worker processes
threads = 4                          # threads per worker (used by gthread)
timeout = 120
worker_class = "sync"
malloc_arena_max = 2                 # cap glibc malloc arenas; 0 = unset
max_requests = 0                     # recycle web worker after N requests to release heap; 0 = disabled
max_requests_jitter = 0              # random +/- spread on max_requests
```

Gunicorn binds automatically to `127.0.0.1:<bench.http_port>` and `preload_app` is always enabled.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `workers` | int | no | `4` | Number of Gunicorn worker processes. |
| `threads` | int | no | `4` | Threads per worker. Used by the `gthread` worker class. |
| `timeout` | int | no | `120` | Request timeout in seconds. |
| `worker_class` | string | no | `sync` | Gunicorn worker class. |
| `malloc_arena_max` | int | no | `2` (new benches); `0` if absent | Caps glibc malloc arenas (`MALLOC_ARENA_MAX`) for the web/companion/worker Python processes to keep idle RSS down. `0` leaves the system default unset. |
| `max_requests` | int | no | `0` | Recycle each web worker after this many requests, re-forking it from the preloaded master to return heap accreted under load. `0` disables it (safe for production); set e.g. `2000` on demo/overcommit benches to bound RSS. |
| `max_requests_jitter` | int | no | `0` | Random ± spread on `max_requests` so workers don't all recycle at once. |

### `letsencrypt` section (new)

```toml
[letsencrypt]
email = "admin@example.com"              # required for ACME account registration
webroot_path = "/var/www/letsencrypt"    # certbot places challenge files here
```

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `email` | string | yes (if any site has `ssl: true`) | — | Contact email registered with Let's Encrypt. |
| `webroot_path` | string | no | `/var/www/letsencrypt` | Directory used for HTTP-01 ACME challenges. Must be served by Nginx at `/.well-known/acme-challenge/`. |

### `admin` section — HTTPS domain

```toml
[admin]
port = 8002
password = "your-admin-password"
domain = "admin.example.com"   # optional
```

When `admin.domain` is set and the bench is deployed to production (`production.enabled = true`):

- `bench setup nginx` generates an nginx server block that proxies the admin port on that domain.
- `bench setup letsencrypt` obtains a certificate for the domain and switches the block to HTTPS.
- HTTP requests to `admin.domain` are redirected to HTTPS automatically.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `domain` | string | no | `""` | Hostname for the admin UI. When set, nginx proxies this domain to the admin port and letsencrypt obtains a cert for it. |

### Validation additions

10. When `production.enabled` is `true`, `production.process_manager` must be `systemd`, `supervisor`, or `openrc`, and `admin.domain` must be set.
11. `letsencrypt.email` must match a basic email pattern if present. Serving SSL sites over HTTPS requires `admin.tls = true` and a `letsencrypt.email`.
12. `admin.domain`, when set, must be a valid hostname.
13. If the bench has wildcard domains configured (see Custom domain management above), a new site's `name` and `admin.domain` must match one of them.

---

## Nginx config structure on disk

```
<bench-root>/
└── config/
    ├── gunicorn.conf.py               # Gunicorn worker/bind configuration
    └── nginx/
        ├── site1.example.com.conf     # per-site server blocks
        ├── site2.example.com.conf
        └── include.conf               # single file symlinked into nginx config_dir
```

`include.conf` contains one line:

```nginx
include /absolute/path/to/bench/config/nginx/*.conf;
```

`bench setup nginx` writes one `include.conf` and symlinks it:

```
/etc/nginx/conf.d/<bench-name>.conf -> <bench-root>/config/nginx/include.conf
```

This means:
- Per-site configs stay inside the bench directory (no root needed to edit them).
- Only the symlink creation and nginx reload require `sudo`.
- Removing a site from `bench.toml` and re-running `bench setup nginx` removes its config file and the dead entry disappears automatically.

---

## Nginx config template

`NginxManager.generate_site_config(site)` produces the following for each site.

### HTTP-only site (`ssl: false`)

```nginx
# bench — site1.example.com

upstream bench-<bench-name> {
    server unix:<bench-root>/config/gunicorn.sock;
    keepalive 32;
}

server {
    listen 80;
    server_name site1.example.com;

    root <bench-root>/sites;
    client_max_body_size 50m;

    location /assets {
        try_files $uri =404;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    location ~ ^/files/.*\.(jpg|jpeg|png|gif|svg|webp|pdf|docx?|xlsx?)$ {
        root <bench-root>/sites/site1.example.com/public;
        try_files $uri =404;
    }

    location /socket.io {
        proxy_pass         http://127.0.0.1:<redis.socketio_port>;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
    }

    location / {
        proxy_pass         http://bench-<bench-name>;
        proxy_read_timeout 120;
        proxy_redirect     off;
        proxy_set_header   Host               $host;
        proxy_set_header   X-Real-IP          $remote_addr;
        proxy_set_header   X-Forwarded-For    $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto  $scheme;
        proxy_set_header   X-Frappe-Site-Name $host;
    }
}
```

### SSL site (`ssl: true`)

When a site has `ssl: true`, two server blocks are generated.

**Block 1 — HTTP (port 80)**: Serves only the ACME challenge path and redirects everything else to HTTPS.

```nginx
server {
    listen 80;
    server_name site1.example.com www.site1.example.com;

    location /.well-known/acme-challenge/ {
        root <letsencrypt.webroot_path>;
        try_files $uri =404;
    }

    location / {
        return 301 https://$host$request_uri;
    }
}
```

**Block 2 — HTTPS (port 443)**: Terminates TLS, serves assets, proxies to Frappe.

```nginx
server {
    listen 443 ssl http2;
    server_name site1.example.com www.site1.example.com;

    ssl_certificate     /etc/letsencrypt/live/site1.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/site1.example.com/privkey.pem;
    ssl_protocols       TLSv1.2 TLSv1.3;
    ssl_ciphers         ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:
                        ECDHE-ECDSA-AES256-GCM-SHA384:ECDHE-RSA-AES256-GCM-SHA384;
    ssl_prefer_server_ciphers off;
    ssl_session_cache   shared:SSL:10m;
    ssl_session_timeout 1d;

    root <bench-root>/sites;
    client_max_body_size 50m;

    # (same location blocks as HTTP-only config above)
}
```

The cert path `/etc/letsencrypt/live/<primary-domain>/fullchain.pem` uses the site's `name` as the primary domain. All `domains` entries appear as SANs on the same certificate.

### Two-phase config generation

Generating HTTPS config before the certificate exists causes nginx to fail to start. `NginxManager` handles this with two modes:

- `generate_config(ssl_ready=False)` — writes HTTP-only blocks (safe to load before certs exist).
- `generate_config(ssl_ready=True)` — writes the full HTTP-redirect + HTTPS blocks.

`bench setup production` calls them in order:

```
1. generate_config(ssl_ready=False) → nginx reload   # port 80 only
2. LetsEncryptManager.obtain()                        # ACME over port 80
3. generate_config(ssl_ready=True)  → nginx reload   # port 80 + 443
```

---

## Architecture additions

### New config dataclasses

#### `NginxConfig`

```python
@dataclass
class NginxConfig:
    enabled: bool = False
    http_port: int = 80
    https_port: int = 443
    config_dir: Path = Path('/etc/nginx/conf.d')
    worker_processes: str = 'auto'
    client_max_body_size: str = '50m'
```

#### `GunicornConfig`

```python
@dataclass
class GunicornConfig:
    workers: int = 4
    threads: int = 4
    timeout: int = 120
    worker_class: str = 'sync'
    malloc_arena_max: int = 2
    max_requests: int = 0
    max_requests_jitter: int = 0
```

#### `LetsEncryptConfig`

```python
@dataclass
class LetsEncryptConfig:
    email: str = ''
    webroot_path: Path = Path('/var/www/letsencrypt')
```

#### Updated `SiteConfig`

```python
@dataclass
class SiteConfig:
    name: str
    db_name: str
    db_password: str
    apps: List[str]
    domains: List[str] = field(default_factory=list)  # new
    ssl: bool = False                                   # new

    @property
    def all_domains(self) -> List[str]:
        """Return [name] + domains — the full list for server_name and certbot."""
        return [self.name] + self.domains
```

#### Updated `BenchConfig`

```python
@dataclass
class BenchConfig:
    ...
    production: ProductionConfig = field(default_factory=ProductionConfig)
    nginx: NginxConfig = field(default_factory=NginxConfig)
    gunicorn: GunicornConfig = field(default_factory=GunicornConfig)
    letsencrypt: LetsEncryptConfig = field(default_factory=LetsEncryptConfig)
```

### New files in `pilot/config/`

```
pilot/config/
├── ...
├── nginx_config.py          # NginxConfig
├── gunicorn_config.py       # GunicornConfig
└── letsencrypt_config.py    # LetsEncryptConfig
```

### New managers

#### `GunicornManager`

```python
class GunicornManager:
    def __init__(self, bench: Bench): ...

    def generate_config(self) -> None:
        """Write config/gunicorn.conf.py from bench.toml [gunicorn] settings.

        When production.use_companion_manager is true, the config also
        includes companion_workers for an RQ worker-pool (with the scheduler
        embedded) and socket.io.
        """

    def upstream_server(self) -> str:
        """Return the nginx upstream server value for the configured bind."""
```

#### `NginxManager`

```python
class NginxManager:
    def __init__(self, bench: Bench): ...

    def is_installed(self) -> bool:
        """Check if nginx binary is present."""

    def install(self) -> None:
        """
        Install nginx via the system package manager.
        Ubuntu: apt-get install nginx
        macOS:  brew install nginx
        """

    def generate_config(self, ssl_ready: bool = False) -> None:
        """
        Write one .conf file per site into config/nginx/.
        Write config/nginx/include.conf.
        ssl_ready=False: HTTP-only blocks (safe before certs exist).
        ssl_ready=True: HTTP-redirect + HTTPS blocks (requires certs).
        """

    def install_config(self) -> None:
        """
        Create the symlink:
          /etc/nginx/conf.d/<bench-name>.conf -> config/nginx/include.conf
        Requires sudo. Uses os.symlink; removes stale symlink first.
        """

    def reload(self) -> None:
        """
        Test config with `nginx -t`, then reload:
        Ubuntu: systemctl reload nginx
        macOS:  nginx -s reload  (brew services does a full restart; -s reload is faster)
        """

    def cert_path(self, site: SiteConfig) -> Path:
        """
        Return /etc/letsencrypt/live/<site.name>/fullchain.pem.
        Used to check whether a cert exists before enabling SSL blocks.
        """

    def cert_exists(self, site: SiteConfig) -> bool:
        """Return True if both fullchain.pem and privkey.pem exist for this site."""
```

#### `LetsEncryptManager`

```python
class LetsEncryptManager:
    def __init__(self, bench: Bench): ...

    def is_installed(self) -> bool:
        """Check if certbot binary is present."""

    def install(self) -> None:
        """
        Install certbot via the system package manager.
        Ubuntu: apt-get install certbot
        macOS:  brew install certbot
        Note: certbot on macOS is supported for completeness but Let's Encrypt
        requires a publicly reachable server — not a local development machine.
        """

    def ensure_webroot(self) -> None:
        """Create letsencrypt.webroot_path if it does not exist."""

    def obtain(self, site: SiteConfig) -> None:
        """
        Run certbot certonly --webroot for the given site.
        Covers site.all_domains (primary + aliases) as -d arguments.
        Registers --deploy-hook 'systemctl reload nginx' so nginx reloads
        automatically on every future renewal.
        Skips if a valid cert already exists and is not near expiry.
        """

    def obtain_all(self) -> None:
        """
        Call obtain() for every ssl=True site, then for a public admin.domain.
        A failure for one domain is printed and skipped rather than aborting
        the rest; if any failed, raises once at the end naming them.
        """

    def renew(self) -> None:
        """certbot renew --quiet — intended for cron usage."""
```

#### `DomainRouteProvider`

```python
class DomainRouteProvider:
    def __init__(self, bench: Bench): ...

    def generate_dns_records(self, site_name: str, domain: str) -> dict:
        """
        Step 1 of attaching a domain: validate it's free (not on this site,
        another site, this bench's admin domain, or a sibling bench), then
        return {"cname": [...], "a": [...]} record sets, one per validation method.
        """

    def register(self, site_name: str, domain: str) -> None:
        """Step 2: re-validate, verify DNS actually points here, then append to site_config.json domains."""

    def deregister(self, site_name: str, domain: str) -> None:
        """Remove domain from site_config.json domains. Raises if it is the current primary."""

    def domains(self, site_name: str) -> List[str]: ...
    def primary(self, site_name: str) -> Optional[str]: ...

    def set_primary(self, site_name: str, domain: Optional[str]) -> None:
        """Write or clear site_config.json's host_name."""

    @staticmethod
    def wildcard_domains() -> List[str]:
        """
        Wildcard domain patterns (e.g. "*.example.com") this host's
        bench-domain-provider extension offers, or [] if none/not installed.
        Host-level — works even before a bench/site exists.
        """

    @staticmethod
    def proxy_servers() -> List[str]:
        """
        IPs of the edge proxies the extension fronts this bench with, or [] if
        none/not installed. When set, setup-nginx trusts only those IPs (accepts
        connections from them, reads the real client from their X-Forwarded-For,
        and forwards that header unchanged). Host-level.
        """
```

If a `bench-domain-provider` executable is on `PATH`, every method above except `wildcard_domains` and `proxy_servers` (static, host-level queries) delegates to it instead of touching `site_config.json`. The CLI contract that extension must implement — verbs, arguments, stdout, exit codes — is documented in [domain-provider.md](domain-provider.md).

### New commands

```python
class SetupNginxCommand:
    def __init__(self, bench: Bench): ...
    def run(self) -> None: ...

class SetupLetsEncryptCommand:
    def __init__(self, bench: Bench): ...
    def run(self) -> None: ...

class SetupProductionCommand:
    def __init__(self, bench: Bench): ...
    def run(self) -> None: ...
```

### Package layout additions

```
pilot/
└── pilot/
    ├── config/
    │   ├── ...
    │   ├── nginx_config.py
    │   ├── gunicorn_config.py
    │   └── letsencrypt_config.py
    │
    ├── core/
    │   ├── ...
    │   └── domain_controller.py     # DomainRouteProvider
    │
    ├── managers/
    │   ├── ...
    │   ├── nginx_manager.py
    │   ├── gunicorn_manager.py
    │   └── letsencrypt_manager.py
    │
    └── commands/
        ├── ...
        └── setup/
            ├── __init__.py
            ├── nginx.py            # SetupNginxCommand
            ├── letsencrypt.py      # SetupLetsEncryptCommand
            └── production.py       # SetupProductionCommand
```

---

## Commands

### `bench setup nginx`

**Pre-conditions:**
- `production.enabled = true` in `bench.toml` (set by `bench setup production`).
- `bench init` has been run (sites exist).
- Process has `sudo` access.

**Steps:**

```
1.  Validate bench.toml (including production validation rules)
2.  Install nginx if not present
3.  Ensure config/nginx/ directory exists
4.  For each site: generate HTTP-only config (ssl_ready=False even for ssl sites)
    — safe to load before certs exist
5.  If any SSL site already has a cert (re-run scenario): generate ssl_ready=True for that site
6.  Write config/nginx/include.conf
7.  Symlink include.conf into nginx config_dir (sudo)
8.  nginx -t to validate, then systemctl reload nginx
9.  Print the URL(s) for each site
```

Idempotent — re-running after certs are obtained upgrades each site's block to HTTPS automatically (step 5).

### `bench setup letsencrypt`

**Pre-conditions:**
- DNS records for all `ssl: true` sites (and `admin.domain` if set) point to this server.
- `letsencrypt.email` is set in `bench.toml`.

**Steps:**

```
1.  Validate bench.toml
2.  Install certbot if not present
3.  Ensure webroot_path exists (create with mkdir -p)
4.  Regenerate nginx with ssl_ready=False (HTTP-only) and reload
    — ensures all domains have a server block to serve ACME challenges
5.  For each site with ssl: true:
    a.  Run certbot certonly --webroot for site.name + domains
    b.  Skip if cert already exists and expires in > 30 days
    c.  On certbot failure for this site, print a warning and move on to the
        next site instead of aborting
6.  If admin.domain is set: run certbot certonly for that domain (same failure handling)
7.  If any domain from steps 5–6 failed, raise one error naming them all
    — sites that succeeded already have valid certs on disk; rerun the
    command to apply ssl_ready=True for them
8.  Regenerate nginx config with ssl_ready=True and reload (skipped if step 7 raised)
```

Certbot's built-in renewal timer (`certbot.timer` systemd unit, installed with certbot) handles future renewals automatically. The `--deploy-hook` ensures nginx reloads after each renewal.

### `bench setup production`

Orchestrates the full production setup in the correct dependency order.

**Pre-conditions:**
- `[production]` section present in `bench.toml`.
- `bench init` has been run.
- DNS records are configured (required before the letsencrypt step).
- Running on a Linux server (Ubuntu). Exits with an error on macOS.
- `--admin-domain` (or `admin.domain` in `bench.toml`) doesn't clash with a sibling bench or this bench's own sites, and matches one of this bench's wildcard domains if any are configured.

**Steps:**

```
1.  Validate bench.toml
2.  Verify running on Linux (error with helpful message if macOS)
3.  Write "dns_multitenant": 1 into sites/common_site_config.json
4a. If lightweight = false (supervisor):
      Install supervisor package if absent, generate config/supervisor/supervisord.conf,
      start supervisord.
4b. If lightweight = true (systemd):
      Write ~/.config/systemd/user/*.service + *.target, run systemctl --user daemon-reload
      and enable the target.
5.  SetupNginxCommand.run()  (nginx is always part of a production deploy)
6.  If admin.tls = true (and any site has ssl: true): SetupLetsEncryptCommand.run()
7.  Build admin frontend for production.
8.  Print summary: process status, site URLs
```

### `bench restart`

Restarts all bench processes. Works with both supervisor and systemd — auto-detected from the filesystem (no flag required).

```
1. Detect process manager:
   a. If config/supervisor/supervisord.conf exists → supervisor
   b. Else if systemd target is enabled → systemd
   c. Else error: "Run 'bench setup production' first"
2. Regenerate process manager config from bench.toml
3. Reload daemon (supervisorctl reread+update or systemctl daemon-reload)
4. Restart all processes
```

`bench upgrade` also calls restart automatically after pulling new bench-cli code, if a production process manager is configured and running.

### CLI additions

```python
@cli.group()
def setup():
    """Production setup sub-commands."""

@setup.command('nginx')
def setup_nginx(): ...           # SetupNginxCommand(bench).run()

@setup.command('letsencrypt')
def setup_letsencrypt(): ...     # SetupLetsEncryptCommand(bench).run()

@setup.command('production')
def setup_production(): ...      # SetupProductionCommand(bench).run()
```

---

## Admin additions

The admin interface gains two read-only sections for production visibility.

### `GET /ssl` — Certificate status

`NginxManager.cert_exists(site)` and `certbot certificates` output for each SSL-enabled site:

| Site | Domains | Cert expiry | Status |
|------|---------|-------------|--------|
| site1.example.com | site1.example.com, www… | 2025-08-19 | valid |

### `GET /nginx` — Nginx config view

Renders the content of each generated `config/nginx/<site>.conf` as a syntax-highlighted code block. Read-only — no editing via the admin.

---

## Full production bench.toml example

```toml
[bench]
name = "prod-bench"
python = "3.14"

[[apps]]
name = "frappe"
repo = "https://github.com/frappe/frappe"
branch = "version-16"

[[apps]]
name = "erpnext"
repo = "https://github.com/frappe/erpnext"
branch = "version-16"

[mariadb]
root_password = "root_s3cr3t"

[redis]
cache_port = 13000
queue_port = 11000
socketio_port = 12000

[[workers]]
queues = ["default"]
count = 4

[[workers]]
queues = ["short"]
count = 2

[[workers]]
queues = ["long"]
count = 1

[admin]
port = 8002
password = "your-admin-password"
domain = "admin.example.com"    # optional — serve admin UI over HTTPS via nginx

[production]
enabled = true
process_manager = "supervisor"   # systemd | supervisor | openrc
# use_companion_manager = true   # run scheduler/workers/socketio inside gunicorn

[nginx]
client_max_body_size = "100m"

[gunicorn]
workers = 4
threads = 4
timeout = 120

[letsencrypt]
email = "ops@example.com"
```

## Admin UI — process controls

When running in production mode, the Processes page in the admin UI shows three buttons:

- **Start** — starts all bench processes (enabled when all are stopped).
- **Stop** — stops web workers and queues, leaving the admin server running.
- **Restart** — restarts web workers and queues without touching the admin server.

The admin server itself is only restarted by `bench restart` (CLI) or `bench upgrade`.
