# Production

Production mode turns a bench into managed services behind nginx. It is meant for Linux hosts with system privileges available to the current user.

## Config

```toml
[production]
enabled = true
process_manager = "systemd" # or "supervisor"
use_companion_manager = false

[admin]
enabled = true
domain = "admin.example.com"
tls = true
```

`admin.domain` is required when production is enabled. Set `admin.tls = false` when TLS is terminated by an external proxy.

## Setup Flow

```bash
bench setup requirements
bench setup config
bench setup nginx
bench setup production --admin-domain admin.example.com
bench setup letsencrypt
```

`bench setup production` writes process manager config and nginx integration. `bench remove production` removes production deployment files and services while keeping logs, certificates, and admin domain config.

## Process Managers

Supported managers are `systemd` and `supervisor`.

Runtime commands:

- `bench start`
- `bench stop`
- `bench restart`

`bench restart` targets the production workload. Local development start/stop uses bench runtime managers.

## Nginx And TLS

Nginx config is rendered from bench and site state. Regenerate it with `bench setup nginx` or `bench setup config`.

Let's Encrypt setup uses configured domains and should run after nginx is rendered. Site domain changes should reload nginx through site/domain code.

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
