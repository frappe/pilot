# End-to-end tests (Playwright for Python)

Browser-driven tests that exercise the real bench lifecycle through the **admin
UI** - the same path a user takes:

```
bench new  →  setup wizard  →  login  →  create site
            →  install app  →  uninstall app  →  drop site
```

Unlike `tests/integration/` (HTTP-level), these drive the actual Vue admin in a
browser and run each mutation as the UI does - as a background task, waited to
completion. Built on **pytest + pytest-playwright** (sync API).

## Layout

| Path | Purpose |
|------|---------|
| `harness/bench.py`  | `Bench` class: wraps `bench new` / `bench start` (wizard + full), stop, destroy (via `bench drop`). Reads the admin port from `bench.toml`; the admin password is harness-chosen (the wizard sets it). |
| `harness/tasks.py`  | Capture a UI action's `task_id` and poll `/api/v1/tasks/:id` to success. |
| `flows/wizard.py`   | `complete_dev_wizard()` - drives `Setup.vue` for the chosen engine (`db_type="mariadb"`/`"postgres"`), dev mode. |
| `flows/admin.py`    | `login`, `create_site`, `install_custom_app`, `uninstall_app`, `drop_site` + API-based assertions. |
| `conftest.py`       | `bench` + `page` fixtures (module-scoped) and the serial-skip wiring. |
| `specs/test_bench_lifecycle.py` | The one serial lifecycle; the engine (`mariadb` / `postgres`) is selected by env. |

The tests in a module are **serial**: they share one bench and one browser
context (so the login cookie carries across) and the `incremental` marker skips
the remaining steps once one fails.

## Running locally

Prerequisites: MariaDB and/or PostgreSQL packages installed (bench provisions
its own rootless, per-user server from them - see `MariaDBManager`/
`PostgresManager` - so no running system service or pre-set password is
needed), Redis, and `bench` on `PATH` (or set `BENCH_BIN`).

```bash
pip install -e ".[admin,e2e]"      # from the repo root
playwright install chromium        # one-time browser download
cd tests/e2e

# MariaDB bench:
E2E_MARIADB_PASSWORD=admin pytest

# PostgreSQL bench:
E2E_DB_TYPE=postgres E2E_POSTGRES_PASSWORD=admin pytest
```

Watch it run with `pytest --headed` (and `--slowmo 500`). After a run, replay the
full trace (DOM snapshots, screenshots, network) with:

```bash
playwright show-trace test-results/<module>/trace.zip
```

By default the harness does **not** build the admin UI - `bench start` serves the
prebuilt bundle (downloaded for the wizard, fetched by `bench init` for the full
bench). Set `E2E_BUILD_ADMIN=1` to build the admin UI from source instead, so the
run exercises *this branch's* frontend (slower, but required to catch frontend
changes - this is what CI does).

Useful env vars:

| Variable | Default | Meaning |
|----------|---------|---------|
| `BENCH_BIN` | `<repo>/bench` | CLI entry point. |
| `E2E_DB_TYPE` | `mariadb` | Bench database engine: `mariadb` or `postgres`. |
| `E2E_MARIADB_PASSWORD` | `admin` | Root password the wizard sets on bench's own rootless MariaDB server. |
| `E2E_POSTGRES_PASSWORD` | `admin` | Superuser password the wizard sets on bench's own rootless PostgreSQL server (used when `E2E_DB_TYPE=postgres`). |
| `E2E_EXTRA_APP` | `1` | `0` skips the install/uninstall app steps (keeps a run quick). |
| `E2E_EXTRA_APP_NAME` / `_REPO` / `_BRANCH` | `blog` / `frappe/blog` / `develop` | The extra app installed/uninstalled. Point at `erpnext`, `india-compliance`, etc. to widen coverage. |
| `E2E_KEEP_ON_FAILURE` | (set) | On failure the bench is kept for inspection; set to `0` to always clean up. |
| `E2E_BUILD_ADMIN` | off | `1` builds the admin UI from source (wizard + full bench) so the run exercises *this branch's* frontend. Off (default) = the harness never builds and `bench start` serves the prebuilt bundle (faster). |

The suite creates a bench named `e2e-<db_type>` (e.g. `e2e-mariadb`,
`e2e-postgres`) under `benches/` and tears it down with `bench drop` on
teardown. Bench names must start with `e2e-` (the harness refuses to delete
anything else).

## Variants & CI

The lifecycle is one env-driven spec; CI (`.github/workflows/e2e.yml`) runs it as
a **matrix** of parallel jobs:

| Variant | `E2E_DB_TYPE` | `E2E_EXTRA_APP` |
|---------|---------------|----------------|
| `mariadb` | `mariadb` | `1` |
| `postgres` | `postgres` | `1` |

To add another variant, add a row to the matrix `include` (and any system deps
its `if:` step needs). To widen what a variant exercises, flip the env knobs
above - no new spec file required.

## Selector note

Selectors are label/role/text based against the existing markup (no
`data-testid`). If UI copy changes, update the strings in `flows/` - they are
centralized there.
