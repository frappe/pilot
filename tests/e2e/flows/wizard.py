"""Drive Setup.vue to completion for a development bench."""

from __future__ import annotations

from playwright.sync_api import Page, expect

# Long pole: bench init clones the framework and builds the venv.
SETUP_TIMEOUT_MS = 20 * 60_000


class WizardSetupError(Exception):
    """Raised when the setup wizard reaches its failure state."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Setup wizard failed: {detail}")


def complete_dev_wizard(
    page: Page,
    *,
    admin_password: str,
    mariadb_password: str = "",
    db_type: str = "mariadb",
    postgres_password: str = "",
    postgres_admin_user: str = "postgres",
    framework_branch: str | None = None,
) -> None:
    """Complete the development-only setup wizard."""
    # The wizard mounts in a 'loading' state, then resolves to the first step.
    expect(page.get_by_text("Step 1 of", exact=False)).to_be_visible(timeout=30_000)
    page.get_by_label("Admin password").fill(admin_password)
    page.get_by_role("button", name="Next").click()
    # MariaDB and PostgreSQL share one set of fields: generic "Root username" /
    # "Root user password" fields (Setup.vue's showRootUsername).
    _choose_select(page, "Database engine", "PostgreSQL" if db_type == "postgres" else "MariaDB")
    # Root username only renders when it isn't implied (a fresh install
    # defaults to root/postgres).
    if page.get_by_label("Root username").is_visible():
        page.get_by_label("Root username").fill(postgres_admin_user if db_type == "postgres" else "root")
    page.get_by_label("Root user password").fill(
        postgres_password if db_type == "postgres" else mariadb_password
    )
    page.get_by_role("button", name="Verify credentials").click()
    # A wrong password surfaces inline and keeps us on this step.
    expect(page.get_by_text("Incorrect MariaDB credentials.")).to_be_hidden()
    expect(page.get_by_text("Incorrect PostgreSQL credentials.")).to_be_hidden()
    # The wizard always provisions a development bench (no production/process-manager
    # choice - that's a separate `bench setup production` step run from the terminal
    # afterwards). We keep the repo default.
    expect(page.get_by_text("Customize your bench")).to_be_visible(timeout=30_000)
    if framework_branch:
        _choose_select(page, "Frappe branch", framework_branch)

    page.get_by_role("button", name="Set up bench").click()
    # bench init clones the framework and builds the venv; this is the long pole.
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
