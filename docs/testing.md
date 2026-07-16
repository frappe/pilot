# Testing

## Running tests

```
pip install -e ".[test,admin,type-check]"

# Unit tests (mirrors .github/workflows/unit-tests.yml)
pytest tests/ --ignore=tests/integration --ignore=tests/e2e --cov=pilot --cov=admin.backend --cov-report=term-missing

# Integration tests: real process groups, nginx config validation, proxy/firewall behavior
pytest tests/integration

# E2E: browser-driven admin UI lifecycle (needs `playwright install chromium`)
pytest tests/e2e

# Style and types
ruff check pilot admin/backend
mypy pilot admin/backend
```

## Pre-restructure baseline

Recorded before Milestone 10's folder restructuring, as the point full CI is expected to stay green against.

- Unit suite (`--ignore=tests/integration --ignore=tests/e2e`): **1336 passed**, 0 failed.
- Integration suite: 34 tests collected.
- E2E suite: 6 tests collected (parametrized across browsers).
- Coverage across `pilot` and `admin.backend`: **72%** (14587 statements, 4144 missed).
- `ruff check pilot admin/backend tests`: clean.
- `mypy pilot admin/backend`: clean (see `[tool.mypy]` in `pyproject.toml` for the one documented gap).

### Known zero-coverage modules

These aren't gaps in the unit suite's design — each is exercised by `tests/integration` or `tests/e2e` instead, which run a real subprocess or a real browser rather than importing the module in-process:

- `pilot/tasks/jobs/*.py` — each job's `if __name__ == "__main__":` entry point only runs when the task manager actually spawns `python -m pilot.tasks.jobs.<name>_task` as a subprocess; unit tests exercise the job's class directly instead, which covers the class body but not the module-level entry point line.
- `admin/backend/server.py`, `admin/backend/wsgi.py` — process entry points, exercised by integration/e2e runs against a live admin server.

These modules have no test coverage at all today and are a real gap, not an
artifact of the unit/integration/e2e split: `admin/backend/readers/monitoring.py`,
`admin/backend/readers/runtime.py`, `pilot/_secure_exec.py`, and
`pilot/internal/site_session.py`.

## Post-restructure verification

Recorded after Milestone 10's folder restructuring finished (10.1-10.13: task
engine into pilot/tasks, commands/core/managers/integrations split into
per-resource modules, admin views/security/readers reorganized, the frontend
grouped by domain, an import-boundary test added, the unit-test tree mirroring
the source tree, and docs renamed to their finalized names). Same command
invocations as the baseline above; every number matches within noise, which is
what a pure-relocation milestone should produce.

- Unit suite: **1338 passed**, 0 failed (+2 vs. baseline: the new
  `test_import_boundaries.py`).
- Integration suite: 36 tests collected (4 passed, 32 skipped — skips are
  environment-gated, e.g. no real MariaDB/Postgres/nginx on this machine, not
  a restructuring regression).
- E2E suite: 6 tests collected, matching the baseline exactly.
- Coverage across `pilot` and `admin.backend`: **72%** (14606 statements,
  4144 missed) — same zero-coverage modules as the baseline; no new gaps.
- `ruff check pilot admin/backend tests`: clean.
- `mypy pilot admin/backend`: clean (217 source files; same one documented
  gap as the baseline).
- `bench --help` lists the same 27 commands and 3 groups as before every
  pilot/commands/ move (`test_command_discovery_matches_baseline`).
- `admin/frontend`: `npm run build` (2256 modules, no unresolved imports) and
  `npm test` (7/7) both pass after the domain reorganization.
