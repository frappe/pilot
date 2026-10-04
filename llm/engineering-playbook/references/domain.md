# Pilot domain notes

Settled facts as of October 2026. Check current code before relying on a detail. `SPEC.md` is the code contract.

## Product

- Pilot manages local and production Frappe benches on one host: create, run, update and remove benches, sites and apps, with production services (nginx, systemd, certificates, WAF, monitoring).
- Surfaces: the `pilot` CLI, the Flask Admin API (`admin/backend/api/v1`) and the Vue Admin UI (`admin/frontend/dashboard`, `editor`, `in-app-embed`).
- Central integration: telemetry, buckets and domain provider flows. Atlas runs Pilot on VMs to host benches.
- Install modes: released (prebuilt tarball with a `VERSION` file and the compiled UI) and `--dev` (a `develop` clone, no `VERSION`). `pilot.is_dev_build` tells them apart.

## Object model

- `Server`: benches directory, SSH keys, monitoring.
- `Bench`: path, `bench.toml`, apps, sites, runtime, production setup, audit log, task runner.
- `Site`: creation, app install, domains, backups, restore, retention, rename, login URLs.
- `App`: repository state, dependencies, validation, revisions.
- Database engines are bench-level services chosen by `bench.db_type`.

## Configuration

- `bench.toml` per bench; `common_config.toml` for settings shared by every bench in a benches directory (`[mariadb]`, `[postgres]`, `[letsencrypt]`, `[central]`, `[telemetry]`, JWKS). Both go through the config model and the TOML store.
- Patches in `pilot/patches` (listed in `patches.txt`) migrate stored config and state.

## Tasks and migrations

- Long work is a `Task` subclass with `@step` phases and callbacks. `command` is the stable task name stored in records.
- `queue_submission()` reports whether an idempotent submission created a task.
- Updates and site migrations are a `MigrationOperation` record plus small tasks and a state machine in `pilot/core/bench/migration/state.py`. Sites go into maintenance mode first; each site is backed up before migrating; failures stop in `needs_attention` for a person to retry or revert.

## Trust model

- Every bench under one benches directory runs as one host user with passwordless sudo for fixed nginx and certbot commands. That user is effectively root on the host.
- Admin access to one bench equals shell access to every bench in that directory. Benches are a workload unit, not a security boundary.
- Whoever reaches the Admin port before setup finishes owns the bench.

## Accepted risks

Do not re-raise as they stand. Raise again when the assumption fails or a change worsens the risk.

| Risk | Assumption | Revisit when |
|---|---|---|
| Benches in one directory can reach each other. | Isolation needs separate hosts or host users. | Pilot claims per-bench isolation. |
| The host user is root-equivalent through sudo grants and root-parsed nginx config. | One host user owns the whole host. | Untrusted users get bench access on a shared host. |
| The setup wizard trusts the first visitor. | Setup runs only where the operator accepts that. | Setup is exposed on public networks by default. |

## Repositories and commands

- Layout: `pilot/core`, `pilot/managers`, `pilot/tasks`, `pilot/commands`, `pilot/internal/cli`, `pilot/internal/tasks`, `admin/backend`, `admin/frontend`, `tests/` (unit, `integration`, `e2e`), `docs/`, `install.sh`.
- Lint: `uv run ruff check admin pilot tests`. Frontend: Biome.
- Tests: `uv run pytest tests/<path>`; `uv run pytest tests/ --ignore=tests/integration` for broad changes. CI runs unit, integration, e2e and install smoke workflows.
- The top-level `benches/` directory is local data and stays ignored. Never delete data directories.
- Atlas (`benches/*/apps/atlas`) has its own playbook and rules; do not mix them.
