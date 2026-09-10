"""Development bench lifecycle through the admin UI."""

from __future__ import annotations

import os

import pytest
from flows.admin import (
    create_site,
    drop_site,
    installed_apps,
    login,
    open_root,
    site_exists,
)
from flows.wizard import complete_dev_wizard
from harness.tasks import expect_bench_online

pytestmark = pytest.mark.incremental


DB_TYPE = os.environ.get("E2E_DB_TYPE", "mariadb")  # 'mariadb' | 'postgres'
# Distinct name per variant so local runs of different variants don't collide.
BENCH_NAME = f"e2e-{DB_TYPE}"

SITE = "site1.localhost"

def test_completes_setup_wizard(bench, page):
    open_root(page, bench.admin_url)
    try:
        complete_dev_wizard(
            page,
            admin_password=bench.admin_password,
            db_type=DB_TYPE,
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


def test_drops_the_site(bench, page):
    drop_site(page, bench.admin_url, SITE)
    assert not site_exists(page, bench.admin_url, SITE)
