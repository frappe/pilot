"""The events a bench raises for its own feed.

Producers are best-effort: a feed that cannot be written must never take down the
task or the monitoring tick that noticed the problem.
"""

from __future__ import annotations

import logging
import typing
from pathlib import Path

from pilot.core.notification import NotificationStore

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench

# Which part of the dashboard a failed command belongs to. Anything unlisted is
# ordinary task noise and lands under "Tasks".
_CATEGORY_BY_COMMAND = {
    "backup-site": "Sites",
    "delete-backup": "Sites",
    "drop-site": "Sites",
    "new-site": "Sites",
    "new-site-from-backup": "Sites",
    "reinstall-site": "Sites",
    "restore-site": "Sites",
    "setup-letsencrypt": "Sites",
    "migrate": "Updates",
    "migration-backup": "Updates",
    "retry-update": "Updates",
    "revert-apps": "Updates",
    "revert-migration": "Updates",
    "revert-site": "Updates",
    "update": "Updates",
    "update-cli": "Updates",
    "restart-database": "Server",
    "restart-services": "Server",
    "setup-nginx": "Server",
}

# Commands whose failure the user already sees in the page that queued them, or
# that a retry loop is expected to shrug off. Keeping them out of the feed is what
# stops it becoming a second, noisier task list.
_UNREPORTED_COMMANDS = frozenset(
    {
        "build",
        "clear-cache",
        "fetch-all-app-updates",
        "wizard-setup",
    }
)


def task_failed(bench_root: Path, meta: dict) -> None:
    """Record a failed task. Called from the task wrapper, so it runs in the child
    process after the task has already been marked failed."""
    command = str(meta.get("command") or "")
    if not command or command in _UNREPORTED_COMMANDS:
        return

    task_id = str(meta.get("task_id") or "")
    site = (meta.get("args") or {}).get("site")
    try:
        NotificationStore(Path(bench_root) / "logs").create(
            f"{_readable(command)} failed",
            category=_CATEGORY_BY_COMMAND.get(command, "Tasks"),
            event="task_failed",
            severity="Error",
            message=f"{site}: see the task log for the failure." if site else "See the task log for the failure.",
            site=site,
            task_id=task_id or None,
            action_route=f"/insights/tasks/{task_id}" if task_id else None,
        )
    except Exception as exc:
        logging.warning("Notification skipped for failed task %s: %s", task_id, exc)


def record_alert(
    bench: "Bench",
    payload: dict,
    *,
    category: str,
    severity: str,
    title: str,
    site: str | None = None,
) -> bool:
    """Record a sustained alert in the bench's own feed, reporting whether it landed.

    The caller still hands the same payload to `alerts.notify` for the webhook and
    Central fan-out; this is the copy that stays on the bench. A write that failed
    returns False so the caller leaves the condition unrecorded and tries again on
    the next tick, rather than losing the incident until it clears and recurs."""
    try:
        bench.notifications.create(
            title,
            category=category,
            event=str(payload.get("event") or "alert"),
            severity=severity,
            message=payload.get("message"),
            site=site,
            action_route=f"/sites/{site}" if site else "/insights/analytics",
        )
        return True
    except Exception as exc:
        logging.warning("Notification skipped for alert %s: %s", payload.get("event"), exc)
        return False


def backups_failing(bench: "Bench", site: str, failures: int) -> None:
    """Raised once when a site reaches the failure limit since its last successful backup."""
    try:
        bench.notifications.create(
            f"Backups of {site} are failing",
            category="Sites",
            event="backups_failing",
            severity="Error",
            message=f"{failures} backup attempts failed since the last successful backup.",
            site=site,
            action_route=f"/sites/{site}",
        )
    except Exception as exc:
        logging.warning("Notification skipped for failing backups of %s: %s", site, exc)


def _readable(command: str) -> str:
    """`backup-site` -> `Backup site`, so the title reads like a sentence."""
    return command.replace("-", " ").capitalize()
