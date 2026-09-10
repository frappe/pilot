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

## Operational Notes

- Production changes may need non-interactive sudo.
- Generated config belongs under the bench `config/` directory or system config locations managed by the relevant manager.
- Logs should remain available after production removal.
- Database services are selected by `bench.db_type` and configured in `bench.toml`.
