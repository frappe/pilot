"""Drive Setup.vue to completion for a development bench."""

from __future__ import annotations

from playwright.sync_api import Page, expect

# Long pole: pilot init clones the framework and builds the venv.
SETUP_TIMEOUT_MS = 20 * 60_000


class WizardSetupError(Exception):
    """Raised when the setup wizard reaches its failure state."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Setup wizard failed: {detail}")


def complete_dev_wizard(
    page: Page,
    *,
    admin_password: str,
    db_type: str = "mariadb",
    framework_branch: str | None = None,
) -> None:
    """Complete the development-only setup wizard."""
    # Setup needs a session like every other page. The wizard's own ?sid= link is
    # printed by `pilot start`; here we sign in with the password `pilot new` was given.
    page.get_by_placeholder("Password").fill(admin_password)
    page.get_by_role("button", name="Continue").click()
    # The wizard mounts in a 'loading' state, then resolves to the first step.
    expect(page.get_by_text("Step 1 of", exact=False)).to_be_visible(timeout=30_000)
    _choose_select(page, "Database engine", "PostgreSQL" if db_type == "postgres" else "MariaDB")
    # "Use existing database" / "Create new database" need no input here - only
    # "Connect to external database" shows host/user/password fields.
    page.get_by_role("button", name="Next").click()
    # The wizard always provisions a development bench (no production/process-manager
    # choice - that's a separate `pilot setup production` step run from the terminal
    # afterwards). We keep the repo default.
    expect(page.get_by_text("Customize your bench")).to_be_visible(timeout=30_000)
    if db_type == "postgres":
        _expect_branch_locked(page)
    elif framework_branch:
        _choose_combobox_button(page, "Frappe branch", framework_branch)

    page.get_by_role("button", name="Set up bench").click()
    # pilot init clones the framework and builds the venv; this is the long pole.
    expect(page.get_by_text("Setting up your bench")).to_be_visible(timeout=60_000)

    # The wizard resolves to exactly one terminal state: success ("Your bench is
    # ready.") or failure (Setup.vue's failWith() sets an error and shows a "Back
    # to configuration" button). Wait for whichever appears first, so a failed
    # setup fails the test immediately instead of hanging until the success
    # locator times out 45 minutes later.
    ready = page.get_by_text("Your bench is ready.")
    failed = page.get_by_role("button", name="Back to configuration")
    expect(ready.or_(failed).filter(visible=True).first).to_be_visible(timeout=SETUP_TIMEOUT_MS)

    if failed.is_visible():
        raise WizardSetupError(_wizard_error_text(page))


def _wizard_error_text(page: Page) -> str:
    """Best-effort scrape of the on-screen failure detail."""
    candidates = [
        page.get_by_text("Setup failed", exact=False),
        page.locator("pre, code"),
    ]
    for locator in candidates:
        try:
            texts = locator.all_inner_texts()
        except Exception:
            texts = []
        joined = "\n".join(texts).strip()
        if joined:
            return joined[-2000:]
    return "the wizard reported a failure (see the task output below)."


def _choose_select(page: Page, label: str, option_name: str) -> None:
    """Choose a reka-ui Select option by combobox label."""
    page.get_by_role("combobox", name=label).click()
    page.get_by_role("option", name=option_name).click()


def _expect_branch_locked(page: Page) -> None:
    """PostgreSQL benches are locked to the develop branch."""
    picker = page.get_by_role("button", name="Frappe branch")
    expect(picker).to_be_disabled()
    expect(picker).to_contain_text("develop")


def _choose_combobox_button(page: Page, label: str, option_name: str) -> None:
    """Choose an option in a Frappe UI Combobox with `trigger="button"`."""
    page.get_by_role("button", name=label).click()
    page.get_by_role("option", name=option_name, exact=True).click()
