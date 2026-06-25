"""Drives Setup.vue to completion for a development bench.

Mirrors the step machine in admin/frontend/src/pages/Setup.vue. Selectors are
label/role based (frappe-ui FormControl associates labels with inputs); if the
UI copy changes, update the strings here in one place.
"""

from __future__ import annotations

from playwright.sync_api import Page, expect

# Long pole: bench init clones the framework and builds the venv.
SETUP_TIMEOUT_MS = 45 * 60_000


class WizardSetupError(Exception):
    """Raised when the setup wizard reaches its failure state."""

    def __init__(self, detail: str) -> None:
        super().__init__(f"Setup wizard failed: {detail}")


def complete_dev_wizard(
    page: Page,
    *,
    admin_password: str,
    mariadb_password: str,
    db_mode: str = "shared",
    framework_branch: str | None = None,
) -> None:
    """Complete the wizard for a development bench (repo defaults, no volumes).
    The wizard only configures a dev bench — production is a separate
    `bench setup production` step — so there are no domain/TLS/process-manager
    prompts to drive here.

    db_mode:
        'shared'    — validate against an existing system MariaDB (CI default).
        'dedicated' — provision a fresh per-bench MariaDB instance (no ZFS). Use
                      this where there is no shared server on the standard port.
    """
    # The wizard mounts in a 'loading' state, then resolves to the first step.
    expect(page.get_by_text("Step 1 of", exact=False)).to_be_visible(timeout=30_000)

    # ── Step 1: Admin password ──────────────────────────────────────────────────
    page.get_by_label("Admin password").fill(admin_password)
    page.get_by_role("button", name="Next").click()

    # ── Step 2: Database ────────────────────────────────────────────────────────
    # Shared validates against the running system server; dedicated provisions a
    # fresh instance (the entered password becomes its new root password).
    _choose_select(
        page,
        "Database",
        "Dedicated instance" if db_mode == "dedicated" else "Shared system MariaDB",
    )
    page.get_by_label("MariaDB root password").fill(mariadb_password)
    page.get_by_role("button", name="Next").click()
    # A wrong password surfaces inline and keeps us on this step.
    expect(page.get_by_text("Incorrect MariaDB credentials.")).to_be_hidden()

    # ── Step 3: Customize ───────────────────────────────────────────────────────
    # The wizard always provisions a development bench (no production/process-manager
    # choice — that's a separate `bench setup production` step run from the terminal
    # afterwards). We keep the repo default and leave "Use volumes" unchecked, so on
    # the last config step "Set up bench" runs init directly.
    expect(page.get_by_text("Customize your bench")).to_be_visible(timeout=30_000)
    if framework_branch:
        _choose_select(page, "Frappe branch", framework_branch)

    page.get_by_role("button", name="Set up bench").click()

    # ── Running → (Done | Failed) ───────────────────────────────────────────────
    # bench init clones the framework and builds the venv; this is the long pole.
    expect(page.get_by_text("Setting up your bench")).to_be_visible(timeout=60_000)

    # The wizard resolves to exactly one terminal state: success ("Your bench is
    # ready.") or failure (Setup.vue's failWith() sets an error and shows a "Back
    # to configuration" button). Wait for whichever appears first, so a failed
    # setup fails the test immediately instead of hanging until the success
    # locator times out 45 minutes later.
    ready = page.get_by_text("Your bench is ready.")
    failed = page.get_by_role("button", name="Back to configuration")
    expect(ready.or_(failed).first).to_be_visible(timeout=SETUP_TIMEOUT_MS)

    if failed.is_visible():
        raise WizardSetupError(_wizard_error_text(page))


def _wizard_error_text(page: Page) -> str:
    """Best-effort scrape of the on-screen failure detail. Setup.vue auto-expands
    the streamed terminal ("Show details") when it fails, so its text usually
    carries the underlying error; the spec augments this with the task's
    output.log."""
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
    """frappe-ui's FormControl ``type="select"`` is a reka-ui Select: a
    ``<button role="combobox">`` trigger (labelled via a separate ``<label for>``)
    plus a portalled listbox of ``role="option"`` items — not a native
    ``<select>``. ``get_by_label`` won't target the button, so open it by its
    combobox role + accessible name (the accname comes from the ``<label for>``),
    then click the option. ``option_name`` matches by substring (labels carry
    hints like "(recommended)"), so pass a distinctive fragment."""
    page.get_by_role("combobox", name=label).click()
    page.get_by_role("option", name=option_name).click()
