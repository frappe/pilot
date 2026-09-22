# Pilot CLI Spec

Pilot manages local and production Frappe benches with a small object model: `Server`, `Bench`, `Site`, `App`, and the database engines behind a bench.

The command line and Admin API should stay thin. They parse input, authorize the request, start tasks when needed, and delegate work to `pilot.core`.

## Goals

- Create, run, update, and remove benches from a fixed top-level benches directory.
- Make the common Frappe workflow predictable: get apps, create sites, install apps, migrate, build assets, and run production services.
- Keep long work observable through task records, logs, steps, and callbacks.
- Keep host-level concerns on `Server`, bench concerns on `Bench`, and site concerns on `Site`.

## Object Model

- `Server` owns host-wide state: the benches directory, SSH keys, and monitoring.
- `Bench` owns a bench path, `bench.toml`, apps, sites, runtime, production setup, audit log, and task runner.
- `Site` owns site operations: creation, app install/uninstall, domains, backups, restore, retention, rename, and login URLs.
- `App` owns repository state, dependency install, validation, and revision tracking.
- Database engines are bench-level services selected by `bench.db_type`.

Use `Server().bench("name")`, `Bench("name")`, or `Bench(path)` to load an existing bench. Use `bench.site("site.local")` for site objects.

## Public Surfaces

- CLI commands live under `pilot/commands`.
- CLI plumbing lives under `pilot/internal/cli`.
- Admin API routes live under `admin/backend/api/v1`.
- Background work lives under `pilot/tasks`.
- Core implementation lives under grouped folders in `pilot/core`.

Commands and API handlers must not duplicate Frappe, systemd, nginx, database, or filesystem orchestration. Put that behavior on the closest core object.

## Trust Model

Pilot manages benches for one host user. Every bench under a benches directory runs as that user, shares its sudo grants, and can read the neighbouring bench directories. Benches are a unit of workload, not a security boundary.

Assume from this:

- Admin access to one bench is equivalent to shell access as the host user, and to the same access over every other bench in that directory.
- Two workloads that must not reach each other belong on separate hosts, or under separate host users with their own benches directory.
- The host user holds passwordless sudo for a fixed set of nginx and certbot commands (installed by `install.sh`) so production deploys and cert renewals need no prompt. These grants, and the bench-writable nginx config that root parses, mean the host user is effectively root-equivalent on the box; treat one bench's compromise as reaching the whole host, not just its own benches.
- Whoever reaches the Admin port before setup finishes owns the bench, so serve the setup wizard only where you accept that.

## Configuration

Each bench has `bench.toml`. It is read and written through the config model and the TOML store, not by ad hoc string edits.

The stable top-level config groups are:

- `[bench]`
- `[[apps]]`
- `[redis]`
- `[[workers]]`
- `[production]`
- `[lite_mode]`
- `[monitor]`
- `[gunicorn]`
- `[build]`
- `[admin]`
- `[firewall]`
- `[waf]`
- `[s3]`
- `[llm]`

Settings shared by every bench under one benches directory - `[mariadb]`, `[postgres]`, `[letsencrypt]`, `[central]`, `[telemetry]`, and `admin.jwks_url`/`jwks_audience` - live in `common_config.toml` instead, merged in by `BenchConfig` alone. See [Configuration](docs/configuration.md#common-config).

Sites are represented by site directories and bench config records where needed.

## Task Model

Long operations should be `Task` subclasses. Queue them with `SomeTask.queue(bench, ...)` or `SomeTask.queue_submission(bench, ...)`.

Use `@step` for visible progress and `@on_success`, `@on_failure`, or `@on_cancel` methods for task callbacks. Callback decorators take no arguments; the method name becomes the callback operation.

## Documentation Map

- [Architecture](docs/architecture.md)
- [Commands](docs/commands.md)
- [Configuration](docs/configuration.md)
- [Tasks](docs/tasks.md)
- [Migrations](docs/migration.md)
- [Admin API](docs/admin-api.md)
- [Admin UI](docs/admin-ui.md)
- [Production](docs/production.md)
- [Domain Provider](docs/domain-provider.md)
