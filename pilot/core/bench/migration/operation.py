from __future__ import annotations

import importlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal
from urllib.parse import urlsplit

from pilot.core.app import RevisionPin
from pilot.core.bench.migration.state import (
    MigrationState,
    MigrationStateError,
    get_state,
    validate_transition,
)
from pilot.exceptions import BenchError, MigrateError

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
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _load_task(command: str):
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

    @property
    def compare_url(self) -> str | None:
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
class MigrationOperation:
    """Durable owner of an update/site-migrate workflow, advanced one task at a time via `enqueue_next`."""

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

    bench: "Bench" = field(default=None, repr=False, compare=False)  # type: ignore[assignment]
    store: "MigrationStore" = field(default=None, repr=False, compare=False)  # type: ignore[assignment]

    @property
    def is_resolved(self) -> bool:
        return self.state.is_terminal

    @property
    def can_revert(self) -> bool:
        return not self.safeguards_disabled and all(
            site.backup_status == "backed_up" for site in self.sites
        )

    @property
    def resource_keys(self) -> list[str]:
        """Resources every task of this operation locks: bench update + each site."""
        return ["bench:update", *[f"site:{site.name.lower()}" for site in self.sites]]

    def begin(self) -> str:
        """Enter the first phase and queue the first chain task. Returns its id."""
        try:
            self._prepare_sites()
            self._enter_first_phase()
            task_id = self.enqueue_next()
            if task_id is None:
                raise BenchError(f"Migration {self.id} has no work to do.")
        except Exception:
            self._restore_maintenance()
            raise
        self.root_task_id = task_id
        self._save()
        return task_id

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
        return next((site.name for site in self.sites if site.backup_status == "pending"), None)

    def next_migrate_site(self) -> str | None:
        return next((site.name for site in self.sites if site.migration_status == "pending"), None)

    def back_up_site(
        self, name: str, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS
    ) -> None:
        site = self.site(name)
        on_step("backup", f"Backing up {name}")
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
            self._restore_maintenance()
            raise
        self._save()
        if self.next_backup_site() is None:
            self._transition("updating" if self.apps else "migrating")

    def update_apps(self, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS) -> None:
        """Update apps to exactly the revisions captured when this operation was created.

        Never re-resolves the marketplace or a branch tip at this point: the
        backup phase may have taken a while, and code may have moved upstream
        since the user chose these targets.
        """
        on_step("update", "Updating apps")
        filter_set = set(self.apps_filter) if self.apps_filter else None
        pins = {
            revision.name: RevisionPin(kind=revision.target_kind, ref=revision.target_sha)
            for revision in self.apps
            if revision.target_sha
        }
        try:
            self.bench._update_apps(filter_set, on_progress, pins)
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
            raise
        self.apps_updated = True
        self._transition("migrating")
        if self.next_migrate_site() is None:
            self._complete(on_step)

    def migrate_site(
        self, name: str, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS
    ) -> None:
        site = self.site(name)
        on_step("migrate", f"Migrating {name}")
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
            self._complete(on_step)

    def retry_arm(self) -> None:
        """Re-arm the chain from needs_attention so the failed unit runs again."""
        if self.state != "needs_attention":
            raise MigrationStateError(f"Retry is not allowed from state {self.state}")
        phase = self.return_state or "migrating"
        self.diagnosis = None
        self._transition("retrying")
        if phase == "backing_up" and self.failed_site:
            self.site(self.failed_site).backup_status = "pending"
        elif phase == "migrating" and self.failed_site:
            self.site(self.failed_site).migration_status = "pending"
        self.failed_site = None
        self._transition(phase)

    def bypass_patch(self, patch: str, on_progress: OnProgress = _NO_PROGRESS) -> None:
        """Permanently mark one patch as completed for the failed site via Frappe.

        Never auto-retries; the operation stays in needs_attention so the user
        must explicitly choose Retry to continue.
        """
        from pilot.utils import run_command

        if self.state != "needs_attention" or not self.failed_site:
            raise MigrationStateError("Skip patch is only available on a failed migration.")
        diagnosed_patch = (self.diagnosis or {}).get("patch")
        if not diagnosed_patch or patch != diagnosed_patch:
            raise MigrationStateError("Skip patch must match the diagnosed failing patch.")
        on_progress(f"Skipping patch {patch} on {self.failed_site}...")
        command = [
            *self.bench.frappe_call,
            "frappe",
            "--site",
            self.failed_site,
            "bypass-patch",
            patch,
            "--yes",
        ]
        result = run_command(command, cwd=self.bench.sites_path, tee_output=True)
        if result.returncode != 0:
            raise BenchError(
                f"bypass-patch failed for {patch} (exit {result.returncode}). "
                "This Frappe version may not support bypass-patch."
            )
        failed = self.site(self.failed_site)
        failed.touched_tables = sorted(set(failed.touched_tables) | {"tabPatch Log"})
        self.decisions.append(
            {"action": "bypass_patch", "site": self.failed_site, "patch": patch, "at": _now()}
        )
        self._save()
        self._record_audit("bypass_patch", {"site": self.failed_site, "patch": patch})

    def revert_arm(self) -> None:
        """Re-arm from needs_attention/revert_failed and enter the next revert phase."""
        if self.state not in ("needs_attention", "revert_failed"):
            raise MigrationStateError(f"Restore is not allowed from state {self.state}")
        if not self.can_revert:
            raise BenchError("Restore is unavailable: safeguards were not created for this update.")
        self.diagnosis = None
        self._transition(self._next_revert_phase())

    def next_revert_site(self) -> str | None:
        return next(
            (
                site.name
                for site in self.sites
                if site.migration_status != "pending"
                and not self.revert_checkpoints.get(f"site:{site.name}")
            ),
            None,
        )

    def revert_apps(self, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS) -> None:
        on_step("revert_apps", "Reverting app revisions")
        try:
            for app in self.apps:
                on_progress(f"Reverting {app.name} to {app.sha[:8]}...")
                self.bench.app(app.name).checkout_commit(app.sha)
            if self.apps:
                filter_set = set(self.apps_filter) if self.apps_filter else None
                self.bench._reinstall_apps(filter_set, on_progress)
                self.bench._rebuild_assets(filter_set, on_progress)
            self.revert_checkpoints["apps"] = True
        except Exception as error:
            self._fail_revert(error)
            raise
        self._transition(self._next_revert_phase())

    def revert_site(
        self, name: str, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS
    ) -> None:
        site = self.site(name)
        on_step("revert_site", f"Recovering {name}")
        site.migration_status = "recovering"
        self._save()
        try:
            on_progress(f"Restoring database for {name}...")
            tables = site.touched_tables if site.touched_tables_trusted else []
            self.bench.site(name).migration_backup.restore(tables)
            on_progress(f"Clearing cache for {name}...")
            self.bench.site(name).clear_cache()
            site.migration_status = "recovered"
            self.revert_checkpoints[f"site:{name}"] = True
        except Exception as error:
            self._fail_revert(error)
            raise
        self._transition(self._next_revert_phase())

    def restart(self, on_step: OnStep = _NO_STEP) -> None:
        on_step("restart", "Restarting services")
        try:
            self.bench.reload_workers()
            self._restore_maintenance()
            for site in self.sites:
                if site.backup_status == "backed_up":
                    self.bench.site(site.name).migration_backup.discard()
            self.revert_checkpoints["restarted"] = True
        except Exception as error:
            self._fail_revert(error)
            raise
        self._transition("reverted")

    def _next_revert_phase(self) -> str:
        if self.apps and not self.revert_checkpoints.get("apps"):
            return "reverting_apps"
        if self.next_revert_site() is not None:
            return "reverting_sites"
        return "restarting"

    def _fail_revert(self, error: Exception) -> None:
        self.diagnosis = {"message": f"Revert failed: {error}", "output_excerpt": ""}
        self._transition("revert_failed")

    def site(self, name: str) -> SiteProgress:
        for site in self.sites:
            if site.name == name:
                return site
        raise BenchError(f"Site {name!r} is not part of migration {self.id}")

    def _record_audit(self, entry_type: str, fields: dict) -> None:
        from pilot.core.bench.audit_log import AuditLog

        AuditLog(self.bench).append(entry_type, {"operation": self.id, **fields})

    def _enter_first_phase(self) -> None:
        if not self.safeguards_disabled and self.sites:
            self._transition("backing_up")
        else:
            self._transition("updating" if self.apps else "migrating")

    def _enter_needs_attention(self, phase: str, site: str | None) -> None:
        self.return_state = phase
        self.failed_site = site
        self._transition("needs_attention")

    def _prepare_sites(self) -> None:
        for site in self.sites:
            site_obj = self.bench.site(site.name)
            if not site.original_config:
                site.original_config = site_obj.maintenance_settings
            site_obj.set_maintenance_mode(True)
        self._save()

    def _restore_maintenance(self) -> None:
        for site in self.sites:
            if site.original_config:
                self.bench.site(site.name).set_maintenance_settings(site.original_config)

    def _complete(self, on_step: OnStep) -> None:
        on_step("restart", "Restarting services")
        self.bench.reload_workers()
        self._restore_maintenance()
        for site in self.sites:
            if site.backup_status == "backed_up":
                self.bench.site(site.name).migration_backup.discard()
        self._transition("completed")

    def _union_touched_tables(self, site: SiteProgress) -> None:
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
        validate_transition(self.state, target)
        self.state = get_state(target)
        if self.state.starts_work and self.started_at is None:
            self.started_at = _now()
        if self.state.is_terminal:
            self.finished_at = _now()
        self._save()

    def _save(self) -> None:
        self.store.save(self)

    def to_dict(self) -> dict:
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
        }

    @classmethod
    def from_dict(cls, data: dict, bench: "Bench", store: "MigrationStore") -> "MigrationOperation":
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
        )
        operation.bench = bench
        operation.store = store
        return operation
