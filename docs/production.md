# Production

Production mode turns a bench into managed services behind nginx. It is meant for Linux hosts with system privileges available to the current user.

## Config

```toml
[production]
enabled = true
process_manager = "systemd" # or "supervisor"

[admin]
enabled = true
domain = "admin.example.com"
tls = true
```

`admin.domain` is required when production is enabled. Set `admin.tls = false` when TLS is terminated by an external proxy.

## Setup Flow

```bash
pilot setup requirements
pilot setup config
pilot setup nginx
pilot setup production --admin-domain admin.example.com
pilot setup letsencrypt
```

`pilot setup production` writes process manager config and nginx integration. `pilot remove production` removes production deployment files and services while keeping logs, certificates, and admin domain config.

## Process Managers

Supported managers are `systemd` and `supervisor`.

A new bench deploys one bench process plus admin and the two redis servers, because
`[lite_mode] enabled` is the default. Turn lite mode off and the set becomes web,
socketio, admin, workers, and redis - see [Lite Mode](configuration.md#lite-mode).

Runtime commands:

- `pilot start`
- `pilot stop`
- `pilot restart`

`pilot restart` targets the production workload. Local development start/stop uses bench runtime managers.

## Nginx And TLS

Nginx config is rendered from bench and site state. Regenerate it with `pilot setup nginx` or `pilot setup config`.

Let's Encrypt setup uses configured domains and should run after nginx is rendered. Site domain changes should reload nginx through site/domain code.

With local TLS enabled, `pilot setup production` enables SSL for each existing site with a public site name or custom domain.
It then requests a certificate for all public domains on each site. Domains that end in `.localhost` stay excluded.

Public certificate requests need a Let's Encrypt contact email. Pass the email during production setup:

```bash
pilot setup production --admin-domain admin.example.com --tls --letsencrypt-email ops@example.com
```

You can also set `letsencrypt.email` in `common_config.toml` before setup.
When an upstream proxy terminates HTTPS, set `admin.tls = false` so Pilot does not change site SSL settings or request certificates.

## Admin Domain

The Admin backend runs behind nginx in production. The public Admin port and the internal Gunicorn port come from `[admin]`.

When using Central or another upstream proxy, keep the local Admin service private and set TLS according to where HTTPS terminates.

## Firewall And WAF

Firewall and WAF config are bench settings. Settings apply code should delegate to core/managers so API routes do not perform system orchestration directly.

## Build Memory

A full asset build peaks near 1.1GB while an idle bench is around 300MB, so an
unbounded build can exhaust a small host and leave the kernel to kill an unrelated
process, usually the database.

Compiling therefore runs in a transient systemd scope with `MemoryMax` set, and only
one build runs at a time per host. The budget is a share of total memory, clamped by
what is actually available, less a reserve kept for the kernel. When too little memory
is free the build is refused before it starts, and a build that exceeds its budget is
killed and reported as out of memory.

This applies only to builds that compile. Apps with a published assets release download
them and are unaffected, so it is reached by custom apps that ship no assets, or by
`pilot build --force`. Where a host cannot delegate a memory controller to the user
slice the build runs uncapped and logs a warning.

## Redis Memory

Both redis instances carry a `maxmemory` ceiling sized from host memory, so a leak
cannot grow without bound on a small host. They differ in what happens at the ceiling:
the cache uses `allkeys-lru` and drops old keys, while the queue uses `noeviction` and
refuses writes. Evicting from the queue would silently discard background jobs, so it
fails the enqueue instead.

## Operational Notes

- Production changes may need non-interactive sudo.
- Generated config belongs under the bench `config/` directory or system config locations managed by the relevant manager.
- Logs should remain available after production removal.
- Database services are selected by `bench.db_type` and configured in `bench.toml`.
