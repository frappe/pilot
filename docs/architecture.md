# Architecture

Bench CLI is organized around a few domain objects. External surfaces should delegate to those objects instead of doing orchestration directly.

## Directory Map

```text
pilot/
  core/
    server/      host-level benches, SSH keys, monitoring
    bench/       bench config, inventory, runtime, production, audit
    site/        site apps, domains, backups, retention, rename, login
    app/         app repositories, dependencies, validation, revisions
    database/    database abstraction and engine selection
    adapters/    integrations with external implementations
  managers/      process, environment, database, nginx, cron, WAF helpers
  commands/      command definitions
  internal/cli/  argparse, context, dispatch
  internal/tasks task execution internals
  tasks/         queued long-running operations

admin/backend/
  api/v1/        Flask route groups
  providers/     backend provider integrations

admin/frontend/  Vue Admin UI
```

## Main Objects

`Server` is the host entry point. It resolves the fixed benches directory, returns benches with `Server().bench("name")`, and owns host-wide SSH keys and monitoring concerns.

`Bench` represents one bench path plus `bench.toml`. It owns apps, sites, runtime config, production setup, audit logging, and `bench.tasks`.

`Site` represents one site inside a bench. It owns site app installs, domains, backups, restore, retention, rename, public config, and login URLs.

`App` represents one app repository. It owns cloning, dependency install, validation, revision pins, and app metadata.

Database objects are created from `bench.db_type`. A bench uses one engine for its sites: `mariadb`, `postgres`, or `sqlite`.

## Control Flow

CLI dispatch builds a `CliContext`, resolves the bench when needed, and runs the matching command. Commands parse flags and call `Bench`, `Site`, `App`, or a task class.

Admin API handlers validate auth and request data, then call the same core objects as the CLI. Long work returns a task id instead of blocking the API.

Tasks are dataclass commands with a `run()` method. They are queued through `SomeTask.queue(bench, ...)` and executed by the task runner under `pilot/internal/tasks`.

## Where Code Belongs

- Host-level actions: `pilot/core/server`.
- Bench lifecycle, config, runtime, production: `pilot/core/bench`.
- Site lifecycle, domains, backups, retention: `pilot/core/site`.
- App repository and install concerns: `pilot/core/app`.
- External tools or services: `pilot/managers` or `pilot/core/adapters`.
- CLI parsing and help text: `pilot/commands`.
- API request and response shaping: `admin/backend/api/v1`.

If a command or API route starts doing subprocess, filesystem, nginx, database, or Frappe orchestration, move that behavior into the closest core object.

## State Layout

Bench data lives under the fixed top-level benches directory returned by `pilot.utils.benches_dir()`.

Inside a bench:

```text
apps/       cloned apps
sites/      Frappe sites and assets
env/        Python virtualenv
logs/       process and task logs
config/     generated Frappe, Redis, nginx, and process config
pids/       local process ids
bench.toml  declarative bench config
```

Shared database services use per-user state managed by database managers. The bench config records how the bench connects to the selected engine.

## Integration Points

Domain provider binaries are wrapped by `pilot/core/adapters/domain_provider.py`. They expose DNS/domain behavior without leaking provider-specific code into `Site`.

Nginx, systemd, supervisor, Redis, Python environments, and databases are implemented by managers. Core objects use managers to keep system integration code away from command and API layers.
