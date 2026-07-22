# Bench CLI Spec

Bench CLI manages local and production Frappe benches with a small object model: `Server`, `Bench`, `Site`, `App`, and the database engines behind a bench.

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

## Configuration

Each bench has `bench.toml`. It is read and written through the config model and the TOML store, not by ad hoc string edits.

The stable top-level config groups are:

- `[bench]`
- `[[apps]]`
- `[mariadb]`
- `[postgres]`
- `[redis]`
- `[[workers]]`
- `[production]`
- `[monitor]`
- `[nginx]`
- `[gunicorn]`
- `[letsencrypt]`
- `[admin]`
- `[central]`
- `[firewall]`
- `[waf]`
- `[s3]`

Sites are represented by site directories and bench config records where needed.

## Task Model

Long operations should be `Task` subclasses. Queue them with `SomeTask.queue(bench, ...)` or `SomeTask.queue_submission(bench, ...)`.

Use `@step` for visible progress and `@on_success`, `@on_failure`, or `@on_cancel` methods for task callbacks. Callback decorators take no arguments; the method name becomes the callback operation.

## Documentation Map

- [Architecture](docs/architecture.md)
- [Commands](docs/commands.md)
- [Configuration](docs/configuration.md)
- [Tasks](docs/tasks.md)
- [Admin API](docs/admin-api.md)
- [Admin UI](docs/admin-ui.md)
- [Production](docs/production.md)
- [Domain Provider](docs/domain-provider.md)
