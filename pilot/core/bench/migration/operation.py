from __future__ import annotations

import contextlib
import importlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pilot.config.monitor import get_update_log
from pilot.core.app import RevisionPin
from pilot.core.bench.migration.recovery import MigrationRecovery
from pilot.core.bench.migration.state import (
    MigrationState,
    MigrationStateError,
    get_state,
    validate_transition,
)
from pilot.exceptions import BenchError, MigrateError
from pilot.internal.jsonl_log import utc_now

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.bench.migration.store import MigrationStore

OnStep = Callable[[str, str], object]
OnProgress = Callable[[str], None]

_NO_STEP: OnStep = lambda key, label: None  # noqa: E731
_NO_PROGRESS: OnProgress = lambda message: None  # noqa: E731

_CHAIN_TASKS = {
    "migration-backup": "pilot.tasks.migration_backup.MigrationBackupTask",
    "update": "pilot.tasks.update.UpdateTask",
    "migrate": "pilot.tasks.migrate.MigrateTask",
    "revert-apps": "pilot.tasks.revert_apps.RevertAppsTask",
    "revert-site": "pilot.tasks.revert_site.RevertSiteTask",
    "restart-services": "pilot.tasks.restart_services.RestartServicesTask",
    "complete-migration": "pilot.tasks.complete_migration.CompleteMigrationTask",
}


def _load_task(command: str):
    """The task class for a chain command."""
    module_path, class_name = _CHAIN_TASKS[command].rsplit(".", 1)
    return getattr(importlib.import_module(module_path), class_name)


@dataclass
class AppRevision:
    name: str
    sha: str
    repository_url: str = ""
    updated_sha: str | None = None
    target_sha: str | None = None  # ref (commit or tag) captured at create time; None when unresolved
    target_kind: Literal["tag", "commit"] = "commit"  # how to check target_sha out
    branch: str = ""  # branch to return to on revert, set when a branch switch changed it
    has_migrate_changes: bool = True

    @property
    def compare_url(self) -> str | None:
        """A link that compares the app's old and new revisions, if the provider supports one."""
        target = self.updated_sha or self.target_sha
        if not target:
            return None
        from pilot.integrations.git.base import GitProviderError, normalize_to_https
        from pilot.integrations.git.github import parse_github_owner_repo

        normalized = normalize_to_https(self.repository_url)
        if urlsplit(normalized).hostname != "github.com":
            return None
        try:
            owner, repository = parse_github_owner_repo(normalized)
        except GitProviderError:
            return None
        return f"https://github.com/{owner}/{repository}/compare/{self.sha}...{target}"


@dataclass
class SiteProgress:
    name: str
    original_config: dict = field(default_factory=dict)
    backup_status: str = "pending"  # pending | backing_up | backed_up | failed
    touched_tables: list[str] = field(default_factory=list)  # cumulative across attempts
    touched_tables_trusted: bool = True
    migration_status: str = "pending"  # pending | running | success | failed | recovering | recovered


@dataclass
class MigrationOperation(MigrationRecovery):
    """Durable owner of an update/site-migrate workflow, advanced one task at a time via `enqueue_next`."""

    jobs_wait_seconds = 300

    id: str
    kind: str  # update | site_migrate
    state: MigrationState
    created_at: str
    started_at: str | None
    finished_at: str | None
    apps: list[AppRevision]
    apps_filter: list[str] | None
    sites: list[SiteProgress]
    apps_updated: bool = False
    failed_site: str | None = None
    diagnosis: dict | None = None
    safeguards_disabled: bool = False
    return_state: str | None = None  # phase to resume into on retry
    root_task_id: str | None = None
    task_ids: dict[str, str] = field(default_factory=dict)  # action tasks (retry/revert)
    chain: list[dict] = field(default_factory=list)  # ordered chain task records
    revert_checkpoints: dict = field(default_factory=dict)
    decisions: list = field(default_factory=list)  # user decisions, e.g. patch skips
    is_web_restarted_in_maintenance: bool = False

    bench: "Bench" = field(default=None, repr=False, compare=False)  # type: ignore[assignment]
    store: "MigrationStore" = field(default=None, repr=False, compare=False)  # type: ignore[assignment]

    @property
    def is_resolved(self) -> bool:
        """Whether the migration reached a terminal state."""
        return self.state.is_terminal

    @property
    def can_revert(self) -> bool:
        """Whether every site has a backup to revert to."""
        return not self.safeguards_disabled and all(site.backup_status == "backed_up" for site in self.sites)

    @property
    def needs_app_update(self) -> bool:
        """Whether the apps still need to be updated."""
        return bool(self.apps) and not self.apps_updated

    @property
    def revision_pins(self) -> dict[str, RevisionPin]:
        """The target revision of each app that has one."""
        return {
            revision.name: RevisionPin(kind=revision.target_kind, ref=revision.target_sha)
            for revision in self.apps
            if revision.target_sha
        }

    @property
    def resource_keys(self) -> list[str]:
        """Resources every task of this operation locks: bench update + each site."""
        return ["bench:update", *[f"site:{site.name.lower()}" for site in self.sites]]

    def begin(self, handoff_from: str | None = None) -> str:
        """Enter the first phase and queue the first chain task. Returns its id.
        `handoff_from` is a running task that passes its resources to this chain."""
        self.ensure_disk_space()
        try:
            self._prepare_sites()
            self._enter_first_phase()
            task_id = self.enqueue_next(handoff_from=handoff_from)
            if task_id is None:
                raise BenchError(f"Migration {self.id} has no work to do.")
        except Exception:
            self._restore_maintenance()
            raise
        self.root_task_id = task_id
        self._save()
        return task_id

    def ensure_disk_space(self) -> None:
        """Refuse to start when the bench disk cannot hold every site's recovery backup."""
        from pilot.core.site.backups import ensure_free_space

        if self.safeguards_disabled:
            return
        sites = [self.bench.site(site.name) for site in self.sites]
        needed = sum(
            site.backups.estimated_database_size for site in sites if not site.migration_backup.is_stored_in_s3
        )
        ensure_free_space(self.bench.sites_path, needed, "the backups this migration takes first")

    def enqueue_next(self, handoff_from: str | None = None) -> str | None:
        """Queue the task the current state asks for, or nothing if the chain pauses."""
        step = self.state.next_step(self)
        if step is None:
            return None
        command, args = step
        task_id = _load_task(command).queue(
            self.bench,
            operation_id=self.id,
            resource_key=self.resource_keys,
            resource_handoff_from=handoff_from,
            **args,
        )
        self.chain.append({"command": command, "task_id": task_id, **args})
        self._save()
        return task_id

    def next_backup_site(self) -> str | None:
        """The next site that needs a backup."""
        return next((site.name for site in self.sites if site.backup_status == "pending"), None)

    def next_migrate_site(self) -> str | None:
        """The next site that needs a migrate."""
        return next((site.name for site in self.sites if site.migration_status == "pending"), None)

    def back_up_site(
        self, name: str, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS
    ) -> None:
        """Back up one site before the migrate and record the result."""
        site = self.site(name)
        on_step("backup", f"Backing up {name}")
        self.wait_for_background_jobs("backing_up", [name], on_progress)
        site.backup_status = "backing_up"
        self._save()
        try:
            on_progress(f"Backing up {name}...")
            self.bench.site(name).migration_backup.create(self.id)
            site.backup_status = "backed_up"
        except Exception as error:
            site.backup_status = "failed"
            self.diagnosis = {
                "phase": "backing_up",
                "message": f"Backup failed for {name}: {error}",
                "output_excerpt": "",
            }
            self._enter_needs_attention("backing_up", name)
            self._lift_maintenance()
            raise
        self._save()
        if self.next_backup_site() is None:
            self._transition("updating" if self.needs_app_update else "migrating")

    def update_apps(self, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS) -> None:
        """Update apps to exactly the revisions captured when this operation was created.

        Never re-resolves the marketplace or a branch tip at this point: the
        backup phase may have taken a while, and code may have moved upstream
        since the user chose these targets.
        """
        on_step("update", "Updating apps")
        filter_set = set(self.apps_filter) if self.apps_filter else None
        self.wait_for_background_jobs("updating", [site.name for site in self.sites], on_progress)
        try:
            self.bench._update_apps(filter_set, on_progress, self.revision_pins)
            self.bench._reinstall_apps(filter_set, on_progress)
            self.bench._rebuild_assets(filter_set, on_progress)
            for revision in self.apps:
                revision.updated_sha = self.bench.app(revision.name).installed_hash
        except Exception as error:
            self.diagnosis = {
                "phase": "update",
                "message": str(error),
                "output_excerpt": getattr(error, "output", "") or "",
            }
            self._enter_needs_attention("updating", None)
            # Nothing in this phase writes to a site database, so whatever failed
            # here - a fetch, a check, an install, a build - leaves the sites
            # untouched and going back is a checkout. No user call needed.
            self._transition("reverting_apps")
            raise
        self.apps_updated = True
        self._transition("migrating")
        if self.next_migrate_site() is None:
            self.complete(on_step)

    def migrate_site(
        self, name: str, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS
    ) -> None:
        """Migrate one site and record the result."""
        site = self.site(name)
        on_step("migrate", f"Migrating {name}")
        self.wait_for_background_jobs("migrating", [name], on_progress)
        site.migration_status = "running"
        self._save()
        try:
            on_progress(f"Migrating {name}...")
            self.bench.site(name).migrate()
            site.migration_status = "success"
        except MigrateError as error:
            from pilot.core.bench.migration.diagnosis import diagnose

            site.migration_status = "failed"
            self.diagnosis = diagnose(error.output or "", str(error))
            self._union_touched_tables(site)
            self._enter_needs_attention("migrating", name)
            raise
        finally:
            self._union_touched_tables(site)
            self._save()
        if self.next_migrate_site() is None:
            self.complete(on_step)

    def wait_for_background_jobs(self, phase: str, names: list[str], on_progress: OnProgress) -> None:
        """Drain queued and running jobs while maintenance mode pauses the scheduler."""
        from pilot.managers.processes.local import ProcessManager

        if not names or not ProcessManager.for_bench(self.bench).is_running():
            return
        self._restart_web_in_maintenance(on_progress)
        deadline = time.monotonic() + self.jobs_wait_seconds
        for name in names:
            on_progress(f"Waiting for the background jobs of {name}...")
            try:
                while self.bench.site(name).has_pending_background_jobs:
                    if time.monotonic() >= deadline:
                        raise BenchError(
                            f"{name} still has background jobs after {self.jobs_wait_seconds} seconds."
                        )
                    time.sleep(2)
            except BenchError as error:
                self._pause_before_step(phase, name, f"{error} The step did not start. Retry later.")

    def _pause_before_step(self, phase: str, name: str, message: str) -> None:
        """Stop before a step and ask the user to decide."""
        self.diagnosis = {"phase": phase, "message": message, "output_excerpt": ""}
        self._enter_needs_attention(phase, None if phase == "updating" else name)
        if phase == "backing_up":
            self._lift_maintenance()
        raise BenchError(message)

    def fail_stopped_step(self, task_id: str, phase: str, site_name: str | None, reason: str = "") -> None:
        """Pause an interrupted chain step unless its successor has already taken over."""
        detail = f": {reason.rstrip('.')}" if reason else ""
        if not self.chain or self.chain[-1]["task_id"] != task_id:
            return
        if self.state == "retrying":
            self._enter_needs_attention(self.return_state or "migrating", self.failed_site)
            return
        if self.state in ("reverting_apps", "reverting_sites", "restarting"):
            self._fail_revert(
                BenchError(f"The recovery task stopped{detail}. Restore again to resume from its checkpoint.")
            )
            return
        if self.state not in ("backing_up", "updating", "migrating"):
            return
        self._fail_stopped_forward_step(phase, site_name, detail)

    def _fail_stopped_forward_step(self, phase: str, site_name: str | None, detail: str) -> None:
        """Mark the step that a stopped task left unfinished as failed."""
        if self.state != phase or self._is_step_done(phase, site_name):
            # The step finished, but the task stopped before it queued the next task.
            phase, site_name = str(self.state), None
        elif site_name:
            self._fail_site_step(phase, site_name)
        self.diagnosis = {
            "phase": phase,
            "message": f"The task stopped before it finished{detail}.",
            "output_excerpt": "",
        }
        self._enter_needs_attention(phase, site_name)
        if phase == "backing_up":
            self._lift_maintenance()

    def _fail_site_step(self, phase: str, site_name: str) -> None:
        """Mark the site's backup or migrate step as failed."""
        site = self.site(site_name)
        if phase == "backing_up":
            site.backup_status = "failed"
        elif phase == "migrating":
            site.migration_status = "failed"
            # A revert must restore what the stopped attempt touched too.
            self._union_touched_tables(site)

    def _is_step_done(self, phase: str, site_name: str | None) -> bool:
        """Whether the step already finished."""
        if phase == "updating":
            return self.apps_updated
        if not site_name:
            return False
        site = self.site(site_name)
        if phase == "backing_up":
            return site.backup_status == "backed_up"
        return site.migration_status == "success"

    def site(self, name: str) -> SiteProgress:
        """The progress of one site in this migration."""
        for site in self.sites:
            if site.name == name:
                return site
        raise BenchError(f"Site {name!r} is not part of migration {self.id}")

    def _enter_first_phase(self) -> None:
        """Move to the first phase: backups, app update or migrate."""
        if not self.safeguards_disabled and self.sites:
            self._transition("backing_up")
        else:
            self._transition("updating" if self.needs_app_update else "migrating")

    def _enter_needs_attention(self, phase: str, site: str | None) -> None:
        """Move to needs_attention and remember where to return."""
        self.return_state = phase
        self.failed_site = site
        self._transition("needs_attention")

    def _prepare_sites(self) -> None:
        """Save each site's maintenance settings and turn maintenance on."""
        for site in self.sites:
            site_obj = self.bench.site(site.name)
            if not site.original_config:
                site.original_config = site_obj.maintenance_settings
            site_obj.set_maintenance_mode(True)
        self.is_web_restarted_in_maintenance = False
        self._save()

    def _restore_maintenance(self) -> None:
        """Restore each site's maintenance settings."""
        for site in self.sites:
            site_obj = self.bench.site(site.name)
            # A site dropped after the failure has no config left to restore.
            if site.original_config and site_obj.exists:
                site_obj.set_maintenance_settings(site.original_config)

    def _discard_snapshots(self) -> None:
        """Discard the migration backups of the sites."""
        for site in self.sites:
            site_obj = self.bench.site(site.name)
            if site.backup_status == "backed_up" and site_obj.exists:
                site_obj.migration_backup.discard()

    def _lift_maintenance(self) -> None:
        """Restore maintenance settings and refresh web workers."""
        self._restore_maintenance()
        self.bench.reload_workers(web_only=True, raises=True)

    def _restart_services(self) -> None:
        """Restart every process on the app code now on disk, then lift maintenance."""
        # The apps may declare different processes now; the restart then restarts them.
        self.bench.processes.apply()
        self.bench.reload_workers(raises=True)
        self._lift_maintenance()

    def _restart_web_in_maintenance(self, on_progress: OnProgress) -> None:
        """Refresh Frappe's 60-second site-config cache before changing site data."""
        if self.is_web_restarted_in_maintenance:
            return
        on_progress("Restarting web so it stops serving the sites...")
        self.bench.reload_workers(web_only=True, raises=True)
        self.is_web_restarted_in_maintenance = True
        self._save()

    def complete(self, on_step: OnStep = _NO_STEP) -> None:
        """Finish a migration whose sites all migrated."""
        if self.state != "migrating" or any(site.migration_status != "success" for site in self.sites):
            raise MigrationStateError("Finish every site migration before completing the update.")
        on_step("restart", "Restarting services")
        try:
            self._restart_services()
        except Exception as error:
            message = f"Restarting services failed: {error}. Retry to restart them again."
            self.diagnosis = {"phase": "migrating", "message": message, "output_excerpt": ""}
            self._enter_needs_attention("migrating", None)
            raise
        self._discard_snapshots()
        self._transition("completed")

    def _union_touched_tables(self, site: SiteProgress) -> None:
        """Add the tables a site's migrate touched to its record."""
        path = self.bench.site(site.name).path / "touched_tables.json"
        if not path.exists():
            site.touched_tables_trusted = False
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            site.touched_tables_trusted = False
            return
        if not isinstance(data, list):
            site.touched_tables_trusted = False
            return
        site.touched_tables = sorted(set(site.touched_tables) | {str(t) for t in data})

    def _transition(self, target: str) -> None:
        """Move to the target state and record start and end times."""
        validate_transition(self.state, target)
        self.state = get_state(target)
        if self.state.starts_work and self.started_at is None:
            self.started_at = utc_now()
        if self.state.is_terminal:
            self.finished_at = utc_now()
        self._save()
        if self.state.is_terminal or self.state.name == "needs_attention":
            self._record_update_result()

    def _record_update_result(self) -> None:
        """Write one record for each app when the update fails or ends. The monitor sends them to Datum,
        so Beacon sees one app version that fails on many servers."""
        result = {"time": utc_now(), "status": self.state.name}
        if self.state.name == "needs_attention":
            diagnosis = self.diagnosis or {}
            result.update(
                status="failed",
                phase=diagnosis.get("phase", ""),
                site=self.failed_site or "",
                message=diagnosis.get("message", ""),
                output_excerpt=diagnosis.get("output_excerpt", "")[-4000:],
            )
        log = get_update_log()
        with contextlib.suppress(OSError):
            for app in self.apps:
                target = app.target_sha or ""
                log.append(
                    {
                        **result,
                        "app": app.name,
                        "repository_url": app.repository_url,
                        "from_version": app.sha[:12],
                        "to_version": target if app.target_kind == "tag" else target[:12],
                    }
                )

    def _save(self) -> None:
        """Save the operation."""
        self.store.save(self)

    def to_dict(self) -> dict:
        """The operation as a JSON-ready dict."""
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state.name,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "apps": [vars(app) for app in self.apps],
            "apps_filter": self.apps_filter,
            "apps_updated": self.apps_updated,
            "sites": [vars(site) for site in self.sites],
            "failed_site": self.failed_site,
            "diagnosis": self.diagnosis,
            "safeguards_disabled": self.safeguards_disabled,
            "return_state": self.return_state,
            "root_task_id": self.root_task_id,
            "task_ids": self.task_ids,
            "chain": self.chain,
            "revert_checkpoints": self.revert_checkpoints,
            "decisions": self.decisions,
            "is_web_restarted_in_maintenance": self.is_web_restarted_in_maintenance,
        }

    @classmethod
    def from_dict(cls, data: dict, bench: "Bench", store: "MigrationStore") -> "MigrationOperation":
        """The operation from its saved dict."""
        operation = cls(
            id=data["id"],
            kind=data["kind"],
            state=get_state(data["state"]),
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            apps=[AppRevision(**app) for app in data.get("apps", [])],
            apps_filter=data.get("apps_filter"),
            sites=[SiteProgress(**site) for site in data.get("sites", [])],
            apps_updated=data.get("apps_updated", False),
            failed_site=data.get("failed_site"),
            diagnosis=data.get("diagnosis"),
            safeguards_disabled=data.get("safeguards_disabled", False),
            return_state=data.get("return_state"),
            root_task_id=data.get("root_task_id"),
            task_ids=data.get("task_ids", {}),
            chain=data.get("chain", []),
            revert_checkpoints=data.get("revert_checkpoints", {}),
            decisions=data.get("decisions", []),
            is_web_restarted_in_maintenance=data.get("is_web_restarted_in_maintenance", False),
        )
        operation.bench = bench
        operation.store = store
        return operation
