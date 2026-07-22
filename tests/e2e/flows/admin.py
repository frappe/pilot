"""High-level admin UI actions that wait for background tasks."""

from __future__ import annotations

import re

from harness.tasks import run_task_action, wait_for_task
from playwright.sync_api import Page, expect


def login(page: Page, base_url: str, password: str) -> None:
    # PUT /api/v1/setup/configuration hands back a session cookie, which carries over
    # in this shared browser context - so without clearing it we'd already be
    # authenticated and the login form would never render. Drop it to exercise the
    # actual login.
    page.context.clear_cookies()
    page.goto(f"{base_url}/")
    page.get_by_placeholder("Password").fill(password)
    page.get_by_role("button", name="Continue").click()
    # Landed on the Sites page once the header action is mounted.
    expect(page.get_by_role("button", name="New site")).to_be_visible(timeout=30_000)


def create_site(page: Page, base_url: str, site_name: str) -> None:
    page.goto(f"{base_url}/")
    page.get_by_role("button", name="New site").click()

    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Site name").fill(site_name)

    task_id = run_task_action(
        page,
        "/api/v1/sites",
        lambda: dialog.get_by_role("button", name="Create Site").click(),
    )
    wait_for_task(page.request, base_url, task_id)


def install_custom_app(page: Page, base_url: str, site_name: str, repo: str, branch: str) -> None:
    """Import a repo app, then install it on the selected site."""
    page.goto(f"{base_url}/marketplace?site={site_name}")
    # The marketplace filter bar always shows an "Import app" entry point.
    page.get_by_role("button", name="Import app").click()

    dialog = page.get_by_role("dialog")
    dialog.get_by_label("Repository URL").fill(repo)
    dialog.get_by_role("button", name="Fetch branches").click()

    branch_box = dialog.get_by_role("combobox", name="Branch")
    expect(branch_box).to_be_visible(timeout=30_000)
    branch_box.click()
    # The option list is portalled to <body>, outside the dialog's DOM subtree
    # (same as the wizard's Select), so it must be page-scoped, not dialog-scoped.
    page.get_by_role("option", name=branch, exact=True).click()

    # Selecting a branch resolves the app name in the background; "Import app"
    # only enables once that succeeds. Capture the resolved name (it may differ
    # from the repo URL) so the marketplace card can be found afterwards.
    add_button = dialog.get_by_role("button", name="Import app")
    expect(add_button).to_be_enabled(timeout=30_000)
    # A leading icon sits before this text node, so its content is " Found
    # <name>" (not anchored at the start) - match unanchored and split instead.
    found_text = dialog.get_by_text(re.compile(r"Found \S")).inner_text()
    app_name = found_text.split("Found ", 1)[1].strip()

    add_task_id = run_task_action(page, "/api/v1/apps", lambda: add_button.click())
    wait_for_task(page.request, base_url, add_task_id)

    # Adding lands on the task's detail page; the newly-cloned app now shows up
    # under "Custom Apps" back on the marketplace, ready to install.
    page.goto(f"{base_url}/marketplace?site={site_name}")
    card = (
        page.locator("div")
        .filter(has_text=re.compile(rf"\b{re.escape(app_name)}\b", re.IGNORECASE))
        .filter(has=page.get_by_role("button", name="Install"))
        .last
    )
    card.get_by_role("button", name="Install").click()

    install_dialog = page.get_by_role("dialog")
    install_task_id = run_task_action(
        page,
        "/api/v1/sites/",
        lambda: install_dialog.get_by_role("button", name="Install", exact=True).click(),
    )
    wait_for_task(page.request, base_url, install_task_id)


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
        "/api/v1/sites/",
        lambda: dialog.get_by_role("button", name="Uninstall", exact=True).click(),
        method="DELETE",
    )
    wait_for_task(page.request, base_url, task_id)


def drop_site(page: Page, base_url: str, site_name: str) -> None:
    # Drop lives in the site's Danger section, part of the "settings" tab (there
    # is no standalone "actions" tab anymore).
    _open_site_tab(page, base_url, site_name, "settings")
    page.get_by_role("button", name="Drop site").click()

    dialog = page.get_by_role("dialog")
    dialog.get_by_label(f"Type {site_name} to confirm").fill(site_name)
    task_id = run_task_action(
        page,
        f"/api/v1/sites/{site_name}",
        lambda: dialog.get_by_role("button", name="Delete site").click(),
        method="DELETE",
    )
    wait_for_task(page.request, base_url, task_id)


def installed_apps(page: Page, base_url: str, site_name: str) -> list[str]:
    res = page.request.get(f"{base_url}/api/v1/sites/{site_name}")
    expect(res).to_be_ok()
    return res.json().get("installed_apps") or []


def site_exists(page: Page, base_url: str, site_name: str) -> bool:
    res = page.request.get(f"{base_url}/api/v1/sites")
    if not res.ok:
        return False
    return any(s.get("name") == site_name for s in res.json())


def _open_site_tab(page: Page, base_url: str, site_name: str, tab: str) -> None:
    # The tab is a router path param (/sites/:name/:tab?), not a URL hash, so
    # navigating straight to it is the most deterministic way to land there.
    page.goto(f"{base_url}/sites/{site_name}/{tab}")
    expect(page.get_by_text(site_name, exact=False).first).to_be_visible(timeout=30_000)
