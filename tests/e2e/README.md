# End-to-end tests (Playwright for Python)

Browser-driven tests that exercise the real bench lifecycle through the **admin
UI** — the same path a user takes:

```
bench new  →  setup wizard  →  login  →  create site
            →  install app  →  uninstall app  →  drop site
```

Unlike `tests/integration/` (HTTP-level), these drive the actual Vue admin in a
browser and run each mutation as the UI does — as a background task, waited to
completion. Built on **pytest + pytest-playwright** (sync API).

## Layout

| Path | Purpose |
|------|---------|
| `harness/bench.py`  | `Bench` class: wraps `bench new` / `bench start` (wizard + full), stop, destroy. Reads the admin port/password from the generated `bench.toml`. |
| `harness/tasks.py`  | Capture a UI action's `task_id` and poll `/api/tasks/:id` to success. |
| `flows/wizard.py`   | `complete_dev_wizard()` — drives `Setup.vue` (shared MariaDB, dev mode). |
| `flows/admin.py`    | `login`, `create_site`, `install_custom_app`, `uninstall_app`, `drop_site` + API-based assertions. |
| `conftest.py`       | `bench` + `page` fixtures (module-scoped) and the serial-skip wiring. |
| `specs/test_*.py`   | One serial lifecycle per bench variant. |

The tests in a module are **serial**: they share one bench and one browser
context (so the login cookie carries across) and the `incremental` marker skips
the remaining steps once one fails.

## Running locally

Prerequisites: a running system MariaDB (root password below), Redis, and
`bench` on `PATH` (or set `BENCH_BIN`).

```bash
pip install -e ".[admin,e2e]"      # from the repo root
playwright install chromium        # one-time browser download
cd tests/e2e

# Shared system MariaDB on the standard port (CI default):
E2E_MARIADB_PASSWORD=admin pytest

# Or, where there is no shared server (e.g. a box whose MariaDB runs as
# per-bench dedicated instances), provision a dedicated instance instead:
E2E_DB_MODE=dedicated E2E_MARIADB_PASSWORD=admin pytest
```

Watch it run with `pytest --headed` (and `--slowmo 500`). After a run, replay the
full trace (DOM snapshots, screenshots, network) with:

```bash
playwright show-trace test-results/<module>/trace.zip
```

By default the harness does **not** build the admin UI — `bench start` serves the
prebuilt bundle (downloaded for the wizard, fetched by `bench init` for the full
bench). Set `E2E_BUILD_ADMIN=1` to build the admin UI from source instead, so the
run exercises *this branch's* frontend (slower, but required to catch frontend
changes — this is what CI does).

Useful env vars:

| Variable | Default | Meaning |
|----------|---------|---------|
| `BENCH_BIN` | `<repo>/bench` | CLI entry point. |
| `E2E_DB_MODE` | `shared` | `shared` validates against system MariaDB; `dedicated` provisions a per-bench instance. |
| `E2E_MARIADB_PASSWORD` | `admin` | Existing system MariaDB root password. |
| `E2E_EXTRA_APP_NAME` / `_REPO` / `_BRANCH` | `blog` / `frappe/blog` / `develop` | The extra app installed/uninstalled. Point at `erpnext`, `india-compliance`, etc. to widen coverage. |
| `E2E_KEEP_ON_FAILURE` | (set) | On failure the bench is kept for inspection; set to `0` to always clean up. |
| `E2E_BUILD_ADMIN` | off | `1` builds the admin UI from source (wizard + full bench) so the run exercises *this branch's* frontend. Off (default) = the harness never builds and `bench start` serves the prebuilt bundle (faster). |

The suite creates a bench named `e2e-<db_mode>` (e.g. `e2e-shared`) under
`benches/` and removes it on teardown. Bench names must start with `e2e-` (the
harness refuses to delete anything else).

## Adding a variant (e.g. ZFS / dedicated DB)

Copy `specs/test_shared_db_lifecycle.py`, change the module constants
(`DB_MODE` / `BENCH_NAME`) and adjust the wizard call (`complete_dev_wizard`
takes `db_mode="dedicated"`). The `bench` fixture reads `BENCH_NAME` straight off
the module, so a new variant is just new constants + the same step functions. Add
snapshot/rollback steps against `/snapshots` for the ZFS case.

## Selector note

Selectors are label/role/text based against the existing markup (no
`data-testid`). If UI copy changes, update the strings in `flows/` — they are
centralized there.
