# Production Setup Specification

Covers DNS-based multitenancy, Nginx reverse-proxy configuration, and Let's Encrypt SSL certificate management. All settings live in `bench.toml`.

> **Platform scope:** Everything in this file targets **Ubuntu/Linux servers**. Let's Encrypt requires a publicly reachable server with real DNS records. Nginx system integration (`/etc/nginx/conf.d/`, `systemctl reload nginx`) is Linux-specific. On macOS, use `bench run` with honcho for development; skip the `bench setup` commands entirely.

---

## Overview

A production bench differs from a development bench in three ways:

1. **Process manager is supervisor** — processes run as a background daemon (see architecture.md).
2. **Nginx sits in front of Gunicorn** — terminates SSL, serves static assets directly, and passes the `Host` header to Frappe to identify the requested site.
3. **Each site has a real domain** — Frappe uses the `Host` header to route requests to the correct site database and files. This is called DNS multitenancy.

The three `bench setup` sub-commands orchestrate these concerns:

| Command | What it does |
|---------|-------------|
| `bench setup nginx` | Generate per-site Nginx config files and install them |
| `bench setup letsencrypt` | Obtain Let's Encrypt certificates for all SSL-enabled sites and the admin domain |
| `bench setup production` | Run both in the correct order; also enables `dns_multitenant` in Frappe |

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

## bench.toml additions

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

### `nginx` section (new)

```toml
[nginx]
enabled = false              # must be set to true for bench setup nginx to proceed
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

When `admin.domain` is set and `nginx.enabled = true`:

- `bench setup nginx` generates an nginx server block that proxies the admin port on that domain.
- `bench setup letsencrypt` obtains a certificate for the domain and switches the block to HTTPS.
- HTTP requests to `admin.domain` are redirected to HTTPS automatically.

| Field | Type | Required | Default | Description |
|-------|------|----------|---------|-------------|
| `domain` | string | no | `""` | Hostname for the admin UI. When set, nginx proxies this domain to the admin port and letsencrypt obtains a cert for it. |

### Validation additions

10. If any `sites[].ssl` is `true`, `nginx.enabled` must be `true` and `letsencrypt.email` must be present.
11. `letsencrypt.email` must match a basic email pattern if present.
12. All entries in `sites[].domains` must be valid hostnames (no spaces, no slashes).
13. `nginx.http_port` and `nginx.https_port` must be distinct integers in the range 1–65535.

---

## Nginx config structure on disk

```
<bench-root>/
└── config/
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
    server 127.0.0.1:8000;
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
    nginx: NginxConfig = field(default_factory=NginxConfig)
    letsencrypt: LetsEncryptConfig = field(default_factory=LetsEncryptConfig)
```

### New files in `bench_cli/config/`

```
bench_cli/config/
├── ...
├── nginx_config.py          # NginxConfig
└── letsencrypt_config.py    # LetsEncryptConfig
```

### New managers

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
        """Call obtain() for every site in bench.config.sites where ssl=True."""

    def renew(self) -> None:
        """certbot renew --quiet — intended for cron usage."""
```

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
bench_cli/
└── bench_cli/
    ├── config/
    │   ├── ...
    │   ├── nginx_config.py
    │   └── letsencrypt_config.py
    │
    ├── managers/
    │   ├── ...
    │   ├── nginx_manager.py
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
- `nginx.enabled = true` in `bench.toml`.
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
6.  If admin.domain is set: run certbot certonly for that domain
7.  Regenerate nginx config with ssl_ready=True and reload
```

Certbot's built-in renewal timer (`certbot.timer` systemd unit, installed with certbot) handles future renewals automatically. The `--deploy-hook` ensures nginx reloads after each renewal.

### `bench setup production`

Orchestrates the full production setup in the correct dependency order.

**Pre-conditions:**
- `nginx.enabled = true` in `bench.toml`.
- `bench init` has been run.
- DNS records are configured (required before `letsencrypt` step).
- Running on a Linux server (Ubuntu). Exits with an error on macOS.

**Steps:**

```
1.  Validate bench.toml
2.  Verify running on Linux (error with helpful message if macOS)
3.  Verify process_manager is supervisor (error if not)
4.  Write "dns_multitenant": 1 into sites/common_site_config.json
5.  Generate supervisor config (SupervisorProcessManager.generate_config())
6.  Start or reload supervisord (SupervisorProcessManager.start())
7.  SetupNginxCommand.run()
8.  SetupLetsEncryptCommand.run()  (skipped if no sites have ssl: true)
9.  Print summary: process status, site URLs
```

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

[workers]
default = 4
short = 2
long = 1

[admin]
port = 8002
password = "your-admin-password"
domain = "admin.example.com"    # optional — serve admin UI over HTTPS via nginx

[nginx]
enabled = true
client_max_body_size = "100m"

[letsencrypt]
email = "ops@example.com"
```
