"""Full happy-path lifecycle for a development bench, driven through the admin UI:

    bench new  →  setup wizard  →  login  →  create site
                →  install app  →  uninstall app  →  drop site

One spec, several variants — selected by env so CI can run them as a matrix:

    E2E_DB_TYPE   mariadb | postgres      (default: mariadb)
    E2E_DB_MODE   shared | dedicated      (default: shared; MariaDB only)
    E2E_EXTRA_APP 0 to skip the install/uninstall app steps (keeps a run quick)

The steps share one bench and one browser context (so the login cookie carries
across, via the module-scoped fixtures in conftest.py) and run serially because
each builds on the last — the `incremental` marker skips the rest once one fails.
"""

from __future__ import annotations

import os

import pytest

from flows.admin import (
    create_site,
    drop_site,
    install_custom_app,
    installed_apps,
    login,
    site_exists,
    uninstall_app,
)
from flows.wizard import complete_dev_wizard
from harness.tasks import expect_bench_online

pytestmark = pytest.mark.incremental


def _truthy(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


# ── variant (override via env to match the CI matrix) ─────────────────────────
DB_TYPE = os.environ.get("E2E_DB_TYPE", "mariadb")  # 'mariadb' | 'postgres'
DB_MODE = os.environ.get("E2E_DB_MODE", "shared")  # 'shared' | 'dedicated' (MariaDB only)
# Distinct name per variant so local runs of different variants don't collide.
BENCH_NAME = f"e2e-postgres-{DB_MODE}" if DB_TYPE == "postgres" else f"e2e-{DB_MODE}"

SITE = "site1.localhost"
MARIADB_PASSWORD = os.environ.get("E2E_MARIADB_PASSWORD", "admin")
POSTGRES_PASSWORD = os.environ.get("E2E_POSTGRES_PASSWORD", "admin")

# An extra app installed from a public repo. Defaults to a light, known-good
# frappe app so CI stays fast; point it at erpnext / india-compliance to widen.
# Set E2E_EXTRA_APP=0 to skip the install/uninstall steps entirely.
INSTALL_EXTRA_APP = _truthy("E2E_EXTRA_APP", "1")
EXTRA_APP_NAME = os.environ.get("E2E_EXTRA_APP_NAME", "blog")
EXTRA_APP_REPO = os.environ.get("E2E_EXTRA_APP_REPO", "https://github.com/frappe/blog")
EXTRA_APP_BRANCH = os.environ.get("E2E_EXTRA_APP_BRANCH", "develop")

_skip_extra_app = pytest.mark.skipif(not INSTALL_EXTRA_APP, reason="E2E_EXTRA_APP=0")


def test_completes_setup_wizard(bench, page):
    page.goto(bench.admin_url)
    try:
        complete_dev_wizard(
            page,
            admin_password=bench.admin_password,
            db_type=DB_TYPE,
            mariadb_password=MARIADB_PASSWORD,
            db_mode=DB_MODE,
            postgres_password=POSTGRES_PASSWORD,
        )
    except Exception as err:
        # Attach the failed setup task's output so the failure is diagnosable
        # straight from the report, not just a "text never appeared" timeout.
        tail = bench.setup_task_error()
        msg = f"{err}\n\n--- setup task output (tail) ---\n{tail}" if tail else str(err)
        raise AssertionError(msg) from err

    # In dev mode the wizard shuts its own server down once init finishes; bring
    # the fully-initialized bench (admin + workers) up for the rest of the run.
    bench.wait_for_wizard_exit()
    bench.start_full()
    expect_bench_online(page.request, bench.admin_url)


def test_logs_into_admin(bench, page):
    login(page, bench.admin_url, bench.admin_password)


def test_creates_a_new_site(bench, page):
    create_site(page, bench.admin_url, SITE)
    assert site_exists(page, bench.admin_url, SITE)
    # A fresh site always has frappe installed.
    assert "frappe" in installed_apps(page, bench.admin_url, SITE)


@_skip_extra_app
def test_installs_extra_app(bench, page):
    install_custom_app(page, bench.admin_url, SITE, EXTRA_APP_REPO, EXTRA_APP_BRANCH)
    assert EXTRA_APP_NAME in installed_apps(page, bench.admin_url, SITE)


@_skip_extra_app
def test_uninstalls_extra_app(bench, page):
    uninstall_app(page, bench.admin_url, SITE, EXTRA_APP_NAME)
    assert EXTRA_APP_NAME not in installed_apps(page, bench.admin_url, SITE)


def test_drops_the_site(bench, page):
    drop_site(page, bench.admin_url, SITE)
    assert not site_exists(page, bench.admin_url, SITE)
