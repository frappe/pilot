"""Shared e2e fixtures for one serial bench lifecycle per module."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from playwright.sync_api import Browser, BrowserContext, Page, expect

from harness.bench import Bench

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "test-results"

# Bound individual UI interactions (click/fill/etc.) and assertions so a wrong
# selector fails in seconds instead of hanging on a multi-minute step. Long waits
# (task polling, the wizard) set their own timeouts.
ACTION_TIMEOUT_MS = 30_000


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.failed and report.when in ("setup", "call"):
        # Remember the first failed step per module for the serial skip + the
        # keep-on-failure teardown decision.
        failed = item.session.__dict__.setdefault("_e2e_failed_step", {})
        failed.setdefault(item.module.__name__, item.name)


def pytest_runtest_setup(item):
    if item.get_closest_marker("incremental") is None:
        return
    failed = getattr(item.session, "_e2e_failed_step", {})
    earlier = failed.get(item.module.__name__)
    if earlier:
        pytest.skip(f"serial: earlier step '{earlier}' failed")


@pytest.fixture(scope="module")
def bench(request) -> Bench:
    """Create a fresh bench, start the wizard, and tear it down after."""
    name = request.module.BENCH_NAME
    extra_env = getattr(request.module, "BENCH_ENV", None)
    b = Bench(name=name, env=extra_env)

    # A previous keep-on-failure run may have left this bench behind; clear it so
    # create() starts clean.
    b.destroy()
    try:
        b.create()
        b.start_wizard()
    except Exception:
        if os.environ.get("E2E_KEEP_ON_FAILURE") == "0":
            b.destroy()
        raise

    yield b

    failed = getattr(request.session, "_e2e_failed_step", {})
    if request.module.__name__ in failed and os.environ.get("E2E_KEEP_ON_FAILURE") != "0":
        # Free ports but keep the bench (and its dedicated MariaDB instance) so a
        # failed run can be inspected. Set E2E_KEEP_ON_FAILURE=0 to clean up.
        b.stop()
        print(f'\n[e2e] Kept bench "{b.name}" at {b.dir} for debugging.')
    else:
        # Full teardown: stop, remove the dedicated MariaDB instance + cnf +
        # systemd override, and delete the bench dir — leave no trace.
        b.destroy()


@pytest.fixture(scope="module")
def context(browser: Browser, request) -> BrowserContext:
    """Module browser context with persistent login and always-on trace."""
    ctx = browser.new_context(ignore_https_errors=True)
    ctx.set_default_timeout(ACTION_TIMEOUT_MS)
    ctx.tracing.start(screenshots=True, snapshots=True, sources=True)

    yield ctx

    out = RESULTS_DIR / request.module.__name__
    out.mkdir(parents=True, exist_ok=True)
    ctx.tracing.stop(path=str(out / "trace.zip"))
    ctx.close()


@pytest.fixture(scope="module")
def page(context: BrowserContext) -> Page:
    expect.set_options(timeout=ACTION_TIMEOUT_MS)
    return context.new_page()
