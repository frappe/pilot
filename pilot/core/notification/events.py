"""The events a bench raises for its own feed and its alert sinks.

Producers are best-effort: a feed or a sink that cannot be reached must never take
down the task or the monitoring tick that noticed the problem.
"""

from __future__ import annotations

import logging
import typing
from pathlib import Path

from pilot.internal.jsonl_log import utc_now

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
    "dismiss-migration": "Updates",
    "revert-site": "Updates",
    "update": "Updates",
    "update-cli": "Updates",
    "restart-database": "Server",
    "restart-services": "Updates",
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


# The event a successful task raises, its title, and whether the bench's own feed shows it.
# Only long operations a user waits for go to the feed; the rest go to webhooks only.
_SUCCESS_EVENTS = {
    "new-site": ("site_created", "Site created", True),
    "new-site-from-backup": ("site_created", "Site created from a backup", True),
    "drop-site": ("site_deleted", "Site deleted", False),
    "rename-site": ("site_renamed", "Site renamed", False),
    "restore-site": ("site_restored", "Site restored", True),
    "reinstall-site": ("site_reinstalled", "Site reinstalled", True),
    "backup-site": ("backup_completed", "Backup completed", False),
    "delete-backup": ("backup_deleted", "Backup deleted", False),
    "install-app": ("app_installed", "App installed", False),
    "get-and-install-app": ("app_installed", "App installed", False),
    "uninstall-app": ("app_uninstalled", "App uninstalled", False),
    "get-app": ("app_added", "App added to the bench", False),
    "remove-app": ("app_removed", "App removed from the bench", False),
    "switch-branch": ("app_branch_switched", "App branch switched", False),
    "setup-letsencrypt": ("certificate_issued", "Certificate issued", False),
    "complete-migration": ("update_completed", "Update finished", True),
    "restart-services": ("update_restored", "Update restored", False),
    "dismiss-migration": ("update_dismissed", "Update dismissed", False),
    "update-cli": ("pilot_updated", "Pilot updated", True),
}

# Task arguments safe to send to a webhook. Passwords, tokens and repository URLs stay out.
_CONTEXT_ARGS = (
    "site",
    "sites",
    "name",
    "new_name",
    "app",
    "apps",
    "branch",
    "marketplace_app",
    "with_files",
    "automatic",
    "filenames",
    "parts",
    "source_site",
    "backup_timestamp",
)


def raise_event(bench: "Bench", *, feed: bool = True, **event: typing.Any) -> None:
    """Write one event to the feed, then send it to the webhook, mail and Central sinks.

    `event` takes the feed fields (`event`, `title`, `category`, `severity`, `message`,
    `site`, `task_id`, `action_route`) and an optional `context` for the sinks.
    `feed=False` sends it to the sinks only."""
    context = {"bench": bench.config.name, "time": utc_now(), **(event.pop("context", None) or {})}
    title = event.pop("title")
    if feed:
        try:
            bench.notifications.create(title, **event)
        except Exception as exc:
            logging.warning("Notification skipped for %s: %s", event.get("event"), exc)
    payload = {**event, "title": title, "message": event.get("message") or title, "context": context}
    try:
        bench.notifier.send(payload)
    except Exception as exc:
        logging.warning("Alert sinks skipped for %s: %s", event.get("event"), exc)


def task_failed(bench_root: Path, meta: dict) -> None:
    """Report a failed task. Called from the task wrapper, so it runs in the child
    process after the task has already been marked failed."""
    command = str(meta.get("command") or "")
    if not command or command in _UNREPORTED_COMMANDS:
        return
    site = _task_site(meta)
    _task_event(
        bench_root,
        meta,
        event="task_failed",
        title=f"{_readable(command)} failed",
        severity="Error",
        message=f"{site}: see the task log for the failure." if site else "See the task log for the failure.",
    )


def task_succeeded(bench_root: Path, meta: dict) -> None:
    """Report a finished operation, such as a new site or a backup, under its own event name."""
    command = str(meta.get("command") or "")
    if command not in _SUCCESS_EVENTS:
        return
    event, title, in_feed = _SUCCESS_EVENTS[command]
    site = _task_site(meta)
    _task_event(
        bench_root,
        meta,
        feed=in_feed,
        event=event,
        title=title,
        severity="Info",
        message=f"{site}: finished." if site else "Finished.",
    )


def _task_event(bench_root: Path, meta: dict, **event: typing.Any) -> None:
    """Add a notification for a task event; log a failure."""
    from pilot.core.bench import Bench

    command = str(meta.get("command") or "")
    task_id = str(meta.get("task_id") or "") or None
    try:
        bench = Bench(Path(bench_root))
    except Exception as exc:
        logging.warning("Notification skipped for task %s: %s", task_id, exc)
        return
    args = meta.get("args") or {}
    raise_event(
        bench,
        **event,
        category=_CATEGORY_BY_COMMAND.get(command, "Tasks"),
        site=_task_site(meta),
        task_id=task_id,
        action_route=f"/insights/tasks/{task_id}" if task_id else None,
        context={
            "command": command,
            "task_id": task_id,
            **{name: args[name] for name in _CONTEXT_ARGS if name in args},
        },
    )


def record_alert(bench: "Bench", payload: dict) -> bool:
    """Record a sustained alert in the bench's own feed, reporting whether it landed.

    The caller still hands the same payload to `Notifier.send` for the webhook and
    Central fan-out; this is the copy that stays on the bench. A write that failed
    returns False so the caller leaves the condition unrecorded and tries again on
    the next tick, rather than losing the incident until it clears and recurs."""
    try:
        bench.notifications.create(
            payload["title"],
            category=payload["category"],
            event=str(payload.get("event") or "alert"),
            severity=payload["severity"],
            message=payload.get("message"),
            site=payload.get("site"),
            action_route=payload.get("action_route"),
        )
        return True
    except Exception as exc:
        logging.warning("Notification skipped for alert %s: %s", payload.get("event"), exc)
        return False


def backups_failing(bench: "Bench", site: str, failures: int) -> None:
    """Raised once when a site reaches the failure limit since its last successful backup."""
    raise_event(
        bench,
        event="backups_failing",
        title=f"Backups of {site} are failing",
        category="Sites",
        severity="Error",
        message=f"{failures} backup attempts failed since the last successful backup.",
        site=site,
        action_route=f"/sites/{site}",
        context={"failures": failures},
    )


def _task_site(meta: dict) -> str | None:
    """The site a task acts on. Tasks that create a site take it as `name`."""
    args = meta.get("args") or {}
    if meta.get("command") in frozenset({"new-site", "new-site-from-backup"}):
        return args.get("name")
    return args.get("site")


def _readable(command: str) -> str:
    """`backup-site` -> `Backup site`, so the title reads like a sentence."""
    return command.replace("-", " ").capitalize()
