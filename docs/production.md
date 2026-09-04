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

`pilot setup letsencrypt` needs `letsencrypt.email`, which is host-shared and lives in `common_config.toml` next to the bench folders (see [Common Config](configuration.md#common-config)), not in `bench.toml`. `pilot setup production --tls --letsencrypt-email <email>` sets it for you.

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
