"""High-level UI actions against the bench admin, expressed the way the spec
reads. Each returns only after the underlying background task has finished, so
callers can assert on a settled bench. ``base_url`` is the admin origin
(http://127.0.0.1:<port>) used for the authenticated task-polling API.
"""

from __future__ import annotations

import re

from playwright.sync_api import Page, expect

from harness.tasks import run_task_action, wait_for_task


def login(page: Page, base_url: str, password: str) -> None:
    # The wizard's /api/setup/save hands back a session cookie, which carries over
    # in this shared browser context — so without clearing it we'd already be
    # authenticated and the login form would never render. Drop it to exercise the
    # actual login.
    page.context.clear_cookies()
    page.goto(f"{base_url}/")
    page.get_by_placeholder("Password").fill(password)
    page.get_by_role("button", name="Login").click()
    # Landed on the Sites page once the header action is mounted.
    expect(page.get_by_role("button", name="New site")).to_be_visible(timeout=30_000)


def create_site(page: Page, base_url: str, site_name: str, db_type: str = "") -> None:
    page.goto(f"{base_url}/")
    page.get_by_role("button", name="New site").click()

    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Site Name").fill(site_name)
    if db_type == "sqlite":
        dialog.get_by_role("button", name="SQLite").click()

    task_id = run_task_action(
        page,
        "/api/sites/create",
        lambda: dialog.get_by_role("button", name="Create Site").click(),
    )
    wait_for_task(page.request, base_url, task_id)


def install_custom_app(page: Page, base_url: str, site_name: str, repo: str, branch: str) -> None:
    """Install an app from a public git repository via the custom-app flow. Using
    an explicit repo/branch keeps the test independent of marketplace registry
    contents."""
    _open_site_tab(page, base_url, site_name, "apps")
    page.get_by_role("button", name="Install App").click()

    dialog = page.get_by_role("dialog")
    dialog.get_by_role("button", name="Install a custom app").click()
    dialog.get_by_label("Repository URL").fill(repo)
    dialog.get_by_label("Branch").fill(branch)

    task_id = run_task_action(
        page,
        "/api/sites/",
        lambda: dialog.get_by_role("button", name="Install", exact=True).click(),
    )
    wait_for_task(page.request, base_url, task_id)


def uninstall_app(page: Page, base_url: str, site_name: str, app_name: str) -> None:
    _open_site_tab(page, base_url, site_name, "apps")

    # The app's row holds its name and a kebab menu button. Match the innermost
    # div containing BOTH (so we skip the text-only and logo-only child divs,
    # which have no button), then open its menu → confirm dialog.
    row = (
        page.locator("div")
        .filter(has_text=re.compile(rf"\b{re.escape(app_name)}\b", re.IGNORECASE))
        .filter(has=page.get_by_role("button"))
        .last
    )
    row.get_by_role("button").last.click()
    page.get_by_role("menuitem", name="Uninstall").click()

    dialog = page.get_by_role("dialog")
    task_id = run_task_action(
        page,
        "/api/sites/",
        lambda: dialog.get_by_role("button", name="Uninstall", exact=True).click(),
    )
    wait_for_task(page.request, base_url, task_id)


def drop_site(page: Page, base_url: str, site_name: str) -> None:
    _open_site_tab(page, base_url, site_name, "actions")
    page.get_by_role("button", name="Drop Site").click()

    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Type the site name to confirm").fill(site_name)
    task_id = run_task_action(
        page,
        "/api/sites/",
        lambda: dialog.get_by_role("button", name="Drop Site").click(),
    )
    wait_for_task(page.request, base_url, task_id)


# ── assertions (read straight from the admin API, authenticated via cookies) ──


def installed_apps(page: Page, base_url: str, site_name: str) -> list[str]:
    # GET /api/sites/<name> nests the site under a "site" key:
    #   { site: { installed_apps: [...] }, installable_apps: [...], ... }
    res = page.request.get(f"{base_url}/api/sites/{site_name}")
    expect(res).to_be_ok()
    return (res.json().get("site") or {}).get("installed_apps") or []


def site_exists(page: Page, base_url: str, site_name: str) -> bool:
    res = page.request.get(f"{base_url}/api/sites/")
    if not res.ok:
        return False
    return any(s.get("name") == site_name for s in res.json())


def _open_site_tab(page: Page, base_url: str, site_name: str, tab: str) -> None:
    # The tab is selected from the URL hash on mount, so navigating is the most
    # deterministic way to land on it.
    page.goto(f"{base_url}/sites/{site_name}#{tab}")
    expect(page.get_by_text(site_name, exact=False).first).to_be_visible(timeout=30_000)
