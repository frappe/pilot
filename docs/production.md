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

`pilot setup production` writes process manager config and nginx integration, and configures telemetry: it fetches this region's Datum credential from Central once, then sets up the metrics monitor and the log shipper from it. `pilot setup telemetry` does the credential and the log shipper on their own. See [Telemetry](configuration.md#telemetry).

`pilot remove production` removes production deployment files and services while keeping logs, certificates, and admin domain config.

## Startup pages

A configured cloud site alias shows a waiting page while its target site is absent. The page refreshes every five seconds. `/api/method/ping` returns 503 until the target exists, so readiness checks do not treat the waiting page as a working site. Unknown hostnames still return 404.

Pilot's MariaDB and PostgreSQL units are `Type=notify`: systemd counts them as started once the server accepts connections, and the web unit starts after that. systemd keeps the web socket open meanwhile, so early requests queue. systemd waits 15 minutes for MariaDB, which extends that time while it recovers from a crash, and sets no time limit for PostgreSQL. A patch rewrites the database units and the bench units from before this change. The patch restarts nothing, so a database unit takes effect at the next restart of the database. Existing external databases have no unit and keep their current startup behavior.

If nginx cannot reach a backend, browser GET requests receive a reconnecting page that refreshes every five seconds. API requests and form submissions retain their gateway error status.

Pilot builds Admin frontends in a separate directory and publishes index.html after the assets. Existing hashed assets remain available to open tabs. If index.html is missing, Admin serves a preparing page until the build is restored.

## Process Managers

Supported managers are `systemd` and `supervisor`.

A new bench deploys one bench process plus admin and the two redis servers, because
`[lite_mode] enabled` is the default. Turn lite mode off and the set becomes web,
socketio, admin, workers, and redis - see [Lite Mode](configuration.md#lite-mode).
Supervisor also runs a `schedule` process; systemd's worker pool runs the scheduler itself.
systemd runs one Frappe `worker-pool` per `[[workers]]` group, so each set of queues keeps its own worker count. A bench with one group has one `worker_pool` unit; a bench with more has `worker_pool_<queues>` units, such as `worker_pool_default` and `worker_pool_long`. The pools share one scheduler, because Frappe's scheduler takes a bench-wide lock. A pool unit sets `KillMode=mixed`, so a stop signals only the pool. The pool then stops each worker once, and a worker finishes its running job before it exits. systemd's default sends SIGTERM to every process, and RQ's job process exits at once on SIGTERM, which lost the running job. systemd still kills what remains after the 90-second stop timeout.

With systemd, the Node socketio process listens on the Unix socket `config/socketio.sock` in the bench, and nginx proxies `/socket.io` to it, so realtime does not answer on a public port. Supervisor, the Python backend, and a Frappe that does not read `FRAPPE_SOCKETIO_UDS` (before v16) keep `socketio_port`. With systemd, nginx tries the socket first and then `socketio_port`, so realtime answers when the realtime process and nginx were configured for different Frappe versions.

Each workload unit sets `LimitNOFILE=65535`. A systemd user unit gets 1024
descriptors by default, which is too few for a lite-mode bench process and for
redis under load.

Runtime commands:

- `pilot start`
- `pilot stop`
- `pilot restart`

`pilot restart` targets the production workload. Local development start/stop uses bench runtime managers.

### Restarts

When apps change (get, remove, update or branch switch), Pilot restarts web, the workers and the app processes. It restarts socketio only when frappe's realtime server or an app's `realtime/` handlers changed after socketio started, so open realtime connections stay up. Redis keeps running and keeps its cache.

With systemd, a socket unit, `<bench>-web.socket`, holds the web port. A web restart, including `pilot restart`, leaves the socket open, so requests wait in its queue while gunicorn starts instead of getting a 502. `pilot restart` restarts the bench's services and not `<bench>.target`, because systemd restarts the socket with the target. `pilot stop` stops the socket too, and a stopped bench keeps it closed. Lite mode's bench process takes the socket as well when its frappe runner accepts one (`LISTEN_FDS`). An older runner binds its own port, and supervisor has no socket units, so their restarts still refuse requests while web starts.

Socketio checks each realtime session against web on `127.0.0.1` (`webserver_host` in `common_site_config.json`), not through nginx and the site's public URL, so the check needs no DNS or trusted TLS certificate. Pilot writes it for new benches and, through a patch, for existing ones, restarting a running production socketio to load it.

Supervisor stops one group at a time: the workload, then the app processes, then redis, and starts them in reverse. A group signals all its programs at once, so redis has its own group to outlive the processes that drain into it.

### Supervisor Extensions

Supervisor benches may define app-specific processes in `config/services/supervisor.d/*.conf`. Pilot includes this directory from its generated `config/services/supervisord.conf` and preserves its fragments when production configuration is regenerated.

After adding or changing a fragment, load it through the bench-owned Supervisor instance:

```bash
supervisorctl -c config/services/supervisord.conf reread
supervisorctl -c config/services/supervisord.conf update
supervisorctl -c config/services/supervisord.conf status
```

Programs declared only in extension fragments are not members of Pilot's generated workload group. Consequently, `pilot restart` does not restart them; manage those programs or their explicitly declared Supervisor group with `supervisorctl`.

## Nginx And TLS

Nginx config is rendered from bench and site state. Regenerate it with `pilot setup nginx` or `pilot setup config`.

All benches on a host share one nginx. A config change holds the host lock `benches/.nginx.lock` from its first write until `nginx -t` passes and nginx reloads, or until Pilot puts back the files that nginx rejected. Another bench's config change waits for the lock, so its `nginx -t` never sees untested files.

Let's Encrypt setup uses configured domains and should run after nginx is rendered. Site domain changes should reload nginx through site/domain code.

With local TLS enabled, `pilot setup production` enables SSL for each existing site with a public site name or custom domain.
It then requests certificates for public domains whose TLS ends on this host. The local-only names `local` and `localhost`, and domains ending in `.local` or `.localhost`, stay excluded. Hostnames matching `bench-domain-provider wildcard-domains` are excluded when they have no explicit route policy. An explicit route with `origin_scheme = "https"` still needs a local certificate; a route with `origin_scheme = "http"` does not. These rules apply to site and Admin hostnames, including older sites with `ssl = true` and no route metadata. A bench with only proxy-terminated hostnames needs no Let's Encrypt contact email.

Public certificate requests need a Let's Encrypt contact email. Pass the email during production setup:

```bash
pilot setup production --admin-domain admin.example.com --tls --letsencrypt-email ops@example.com
```

You can also set `letsencrypt.email` in `common_config.toml` before setup.

Let's Encrypt setup also installs the host-wide user timer `pilot-certificates.timer`. It runs 15 minutes after boot and then daily. It checks each production bench's current local certificate domains and refreshes certificates near expiry or with changed domain coverage. It does not run an unfiltered host-wide `certbot renew`. Proxy-terminated and removed routes are excluded from renewal and expiry alerts. Pilot keeps each certificate's state in the bench's `logs/certificates.json`: the expiry read from the certificate file, and the last certbot error. It never connects to the domain, so the check does not wake a sleeping host. The state is `valid`, `pending`, `failed` (certbot could not issue or renew), `expiring` (less than 14 days left, so renewal is not working), or `expired`. Site > Settings > Domains shows it for each domain, with the certbot error for a failed certificate. A domain whose certificate is failed, expiring or expired raises one alert (`certificate_failed` or `certificate_expiry`) in the bench's notifications and its alert sinks. Pilot alerts again only after the certificate is valid and then breaks again. Its log is `system/logs/certificates.log` in the Pilot directory. Existing certificate files are retained. A separate renewal service installed by the operating system has its own configuration.
When an upstream proxy terminates HTTPS, set `admin.tls = false` so Pilot does not change site SSL settings or request certificates.

Private names and explicit route policies need no wildcard-provider lookup. If a provider lookup or certificate check fails during daily maintenance, Pilot logs the bench path and the error and continues with the other benches. A failed refresh does not prevent the expiry check for that bench.

When requests arrive from configured proxy servers, Pilot preserves the forwarded scheme supplied by an edge that terminates TLS. Socket.IO also preserves an explicit browser `Origin` and reconstructs a same-origin value from the forwarded scheme and host only when that header is absent.

A vhost that receives TLS through PROXY protocol v2 uses its local `$scheme` instead, because the PROXY protocol does not carry `X-Forwarded-Proto`. The scheme is chosen per vhost, so edge-terminated and passthrough vhosts can share one bench.

## Admin Domain

The Admin backend runs behind nginx in production. The public Admin port and the internal Gunicorn port come from `[admin]`.

When using Central or another upstream proxy, keep the local Admin service private and set TLS according to where HTTPS terminates.

## Sandbox

The sandbox runs the bench's workload units (web, socketio, workers, redis and app processes) with `systemd.exec` sandbox directives. It needs `process_manager = "systemd"`, and a systemd bench uses it unless `sandbox = false` is set under `[production]`. A new bench has no setting, so it is sandboxed. A bench that existed before the default changed got `sandbox = false` from the `set_sandbox_off_for_existing_benches` patch; set it to `true` to opt in. A Supervisor bench is never sandboxed. The admin and central bootstrap units stay unsandboxed because the admin runs the nginx and certbot sudo commands.

| Directives | Effect |
|---|---|
| `NoNewPrivileges` | sudo and setuid programs fail. |
| `PrivateUsers` | Needed by the mount directives in a user unit. |
| `ProtectSystem=strict`, `ReadWritePaths=<bench>/<folder>`, `PrivateTmp` | Writes only inside the bench's workload folders. Each unit has its own `/tmp`. |
| `ProtectHome=tmpfs` | The home directory is empty, so SSH keys, other dotfiles and the user bus in `/run/user` are not there to read. |
| `TemporaryFileSystem=/run:ro`, `TemporaryFileSystem=/dev/shm`, `InaccessiblePaths=/var/log/journal` | Host sockets in `/run` are hidden, such as `docker.sock`, `libvirt-sock` and the system D-Bus, which the user's groups could otherwise open. `/run/systemd/resolve` and a database socket folder under `/run` are bound back. Shared memory in `/dev/shm` is private to the unit, and the system journal cannot be read. SysV IPC and `/dev/mqueue` stay shared with the user's processes, because `PrivateIPC` fails to start some user units. |
| `BindReadOnlyPaths=<bench>/config/<entry>` | A workload cannot change the files that the admin trusts and runs outside the sandbox: `services`, `nginx`, `modsecurity`, `letsencrypt`, `frappe_cloud`, `site-renames`, `site-creations`, `site-aliases.json`, `realtime-folders.txt`, the gunicorn configs, `Procfile` and the redis configs. Pilot creates the folders before the units start, so a workload cannot plant one first. The rest of `config/` stays writable, because Frappe creates its lock files there (`config/<name>.lock`) and production realtime creates its socket there (`config/socketio.sock`). Pilot writes the protected files in place, because Linux detaches a file mount once the file under it is renamed over. Workload units never read `bench.toml`: Frappe takes its database settings from `common_site_config.json` and `site_config.json`. |
| `BindReadOnlyPaths=<bench>/sites/common_site_config.json` | A workload cannot change, move or delete the bench-wide settings that every site and the unsandboxed `pilot frappe` commands read. Pilot writes the file in place, so its edits reach running units at once. |
| `BindReadOnlyPaths` | Brings back, from the masked home, only what the bench uses: the Python the venv links to (such as a uv-managed one), the Node in use, nvm (`$NVM_DIR` or `~/.nvm`), the database socket directory, linked apps, and the `read_paths` of apps. The rest of the Pilot install, including the database data files, stays hidden. |
| `TemporaryFileSystem=<benches dir>:ro`, `BindPaths=<bench>/<folder>` | The neighbouring benches and `common_config.toml` are hidden. Of the bench itself, only `apps`, `env`, `sites`, `config`, `logs`, `pids`, `chromium` and `flow-server` are mounted, and Pilot creates them before the units start. So `bench.toml` with the admin password hash and JWT secret, `.credentials`, the admin's `tasks/` and its state files are not there, even after Pilot rewrites them. |
| `SystemCallFilter=@system-service`, `SystemCallArchitectures=native`, `SystemCallErrorNumber=EPERM` | Blocks ptrace, mount, module loading and other privileged syscalls. |
| `ProtectKernelTunables`, `ProtectKernelModules`, `ProtectKernelLogs`, `ProtectControlGroups`, `ProtectClock`, `ProtectHostname` | Kernel settings, logs, cgroups, the clock and the hostname are read-only. |
| `PrivateDevices` | Only pseudo devices such as `/dev/null` are visible. |
| `RestrictNamespaces`, `RestrictRealtime`, `RestrictSUIDSGID`, `LockPersonality` | No new namespaces, realtime scheduling or setuid files. |
| `RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6 AF_NETLINK` | Only local and IP sockets. |

Apps can keep more home paths. Declare `read_paths` and `write_paths` under `[tool.pilot.sandbox]` in `pyproject.toml` for every workload unit, or on a `[tool.pilot.processes.<name>]` table for that unit only. See [App Processes](app-processes.md#sandbox-paths).

Before Pilot replaces the units, it starts a trial unit with the same directives. When the host cannot start it, a bench with no `sandbox` setting runs without the sandbox and logs why. A bench with `sandbox = true` fails setup with the systemd error and keeps its current units; set `sandbox = false` to deploy without the sandbox.

The trial needs unprivileged user namespaces. Ubuntu 24.04 restricts them through AppArmor (`kernel.apparmor_restrict_unprivileged_userns = 1`), so there the trial fails with exit code 226.

With the sandbox on:

- sudo fails in workload processes and app hooks.
- A process can write only inside its bench's workload folders.
- Web and workers no longer share files in `/tmp`.
- A bench cannot read the other benches in its benches directory.

All benches still run as one user. Code running in one bench can still read the files and environment of another bench's process through `/proc/<pid>`, signal its processes, reach its redis on `127.0.0.1` and reach its loopback ports. Put workloads that must not reach each other under separate host users.

## Firewall And WAF

Firewall and WAF config are bench settings. Settings apply code should delegate to core/managers so API routes do not perform system orchestration directly.

## JS Dependencies

Before a build, Pilot installs each app's JS dependencies with the package manager its lockfile names: `pnpm-lock.yaml` uses pnpm, and `yarn.lock` or no lockfile uses yarn. The install uses the frozen lockfile and fails the build when the lockfile is out of date. Pilot checks the app root, the `frontend` and `roster` folders, and each `build_dir` in `[tool.bench.assets]`, and installs where `node_modules` is missing or older than the lockfile. A folder without its own lockfile installs from the app root when the app root workspace lists it. For an app root with `pnpm-lock.yaml`, the folder must be an importer in that lockfile. For other app roots, the folder must match the `workspaces` patterns in `package.json`. Pilot installs pnpm into `~/.local` the first time an app needs it. An app with both lockfiles fails the build.

## Build Memory Cap

Asset builds (`pilot build`, and rebuilds triggered from app updates) run capped at 85% of the host's free memory via a transient systemd scope, so a runaway build fails instead of the kernel picking a victim process, usually the database. Past the RAM cap the build may use the same share of free swap, and the kernel kills the build when it reaches both. A full asset build peaks near 1.6GB, and some app frontends (for example Helpdesk) need more than 2GB, so a host with 4GB of RAM needs swap to build them. When the host has less than 3GB of free memory and `install.sh` installed `/usr/local/sbin/pilot-build-swap` (as root, or through sudo in a normal install), Pilot adds a 2GB swap file (`/var/lib/pilot/build.swap`) for the length of the build and removes it after. Builds that run at the same time share it, and a reboot resets it. MariaDB's unit sets `MemorySwapMax=0`, so the database never swaps. Pilot does not refuse a build up front on a host with little free memory: a light app build fits, and the capped scope kills one that outgrows its cap. Where the host cannot cap a build, it warns and runs the build uncapped.

The cap applies only to hosts with `systemd-run` and cgroup memory delegation available; where neither is available the build runs uncapped with a warning. Concurrent builds are not coordinated - each sizes its cap independently from memory free at the time it starts.

To set a fixed cap instead, add `memory_limit_mb` under `[build]` in `bench.toml`. Leave it unset (or 0) to keep the automatic 85% sizing.

Node.js builds get a heap of 75% of the cap through `NODE_OPTIONS=--max-old-space-size`. A `--max-old-space-size` that you set in `NODE_OPTIONS` takes priority.

## Monitoring

`pilot setup production` installs `pilot-monitor.service`, one user service for the host. It collects host, database and bench metrics every 10 seconds, and it pings every production site every 5 seconds on a separate thread, so a slow metrics tick does not delay the pings. Its output goes to `system/logs/monitor.log` and `system/logs/monitor.error.log`. The service runs for the life of the host, so a Pilot upgrade restarts it to load the new code. Check it with `systemctl --user status pilot-monitor.service`.

## Operational Notes

- Production changes may need non-interactive sudo.
- Central-enabled systemd benches retry the metadata credential at boot until it is applied.
- Generated config belongs under the bench `config/` directory or system config locations managed by the relevant manager.
- Logs should remain available after production removal.
- Database services are selected by `bench.db_type` and configured in `bench.toml`.
