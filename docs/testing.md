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
