from __future__ import annotations

import json
import secrets
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pilot.core.bench.migration.state import (
    COMPLETED,
    MIGRATING,
    NEEDS_ATTENTION,
    RESTORE_FAILED,
    RESTORED,
    RESTORING,
    RETRYING,
    TERMINAL_STATES,
    UPDATING,
    MigrationStateError,
    validate_transition,
)
from pilot.exceptions import BenchError, MigrateError
from pilot.internal.atomic_file import atomic_write_private_text
from pilot.utils import make_private_directory

if TYPE_CHECKING:
    from pilot.core.bench import Bench

OnStep = Callable[[str, str], None]
OnProgress = Callable[[str], None]

_NO_STEP: OnStep = lambda key, label: None  # noqa: E731
_NO_PROGRESS: OnProgress = lambda message: None  # noqa: E731


def _now() -> str:
    return datetime.now(UTC).isoformat()


def generate_operation_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)


@dataclass
class AppRevision:
    name: str
    sha: str


@dataclass
class SiteProgress:
    name: str
    original_config: dict = field(default_factory=dict)
    snapshot_status: str = "skipped"  # pending | created | skipped | failed
    previous_tables: list[str] = field(default_factory=list)
    touched_tables: list[str] = field(default_factory=list)  # cumulative across attempts
    migration_status: str = "pending"  # pending | running | success | failed


@dataclass
class MigrationOperation:
    """Durable owner of an update/site-migrate workflow's progress and recovery state.

    One process owns the file; tasks are thin adapters that call the execute_* methods.
    """

    id: str
    kind: str  # update | site_migrate
    state: str
    created_at: str
    started_at: str | None
    finished_at: str | None
    apps: list[AppRevision]
    apps_filter: list[str] | None
    sites: list[SiteProgress]
    failed_site: str | None = None
    diagnosis: dict | None = None
    safeguards_disabled: bool = False
    skip_failing_patches: bool = False
    root_task_id: str | None = None
    task_ids: dict[str, str] = field(default_factory=dict)
    restore_checkpoints: dict = field(default_factory=dict)

    # Non-persisted runtime wiring.
    bench: "Bench" = field(default=None, repr=False, compare=False)  # type: ignore[assignment]
    store: "MigrationStore" = field(default=None, repr=False, compare=False)  # type: ignore[assignment]

    @property
    def is_resolved(self) -> bool:
        return self.state in TERMINAL_STATES

    def execute_update(self, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS) -> None:
        on_step("safeguards", "Preparing recovery snapshots")
        if not self._prepare_safeguards(on_progress):
            return
        self._transition(UPDATING)
        filter_set = set(self.apps_filter) if self.apps_filter else None
        on_step("update", "Updating apps")
        self.bench._update_apps(filter_set, on_progress)
        self.bench._reinstall_apps(filter_set, on_progress)
        self.bench._rebuild_assets(filter_set, on_progress)
        self._migrate_pending(on_step, on_progress)

    def execute_site_migrate(self, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS) -> None:
        on_step("safeguards", "Preparing recovery snapshot")
        if not self._prepare_safeguards(on_progress):
            return
        self._migrate_pending(on_step, on_progress)

    @property
    def can_restore(self) -> bool:
        return not self.safeguards_disabled and all(
            site.snapshot_status == "created" for site in self.sites
        )

    def retry(self, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS) -> None:
        """Re-migrate from the failed site, skipping already-successful sites."""
        if self.state != NEEDS_ATTENTION:
            raise MigrationStateError(f"Retry is not allowed from state {self.state}")
        self.diagnosis = None
        self.failed_site = None
        self._transition(RETRYING)
        self._migrate_pending(on_step, on_progress)

    def restore(self, on_step: OnStep = _NO_STEP, on_progress: OnProgress = _NO_PROGRESS) -> None:
        """Roll apps back to captured revisions and selectively reverse DB changes.

        Resumable: each step is checkpointed, so a restore that fails leaves the
        operation in restore_failed and can be re-run without repeating work.
        """
        if self.state not in (NEEDS_ATTENTION, RESTORE_FAILED):
            raise MigrationStateError(f"Restore is not allowed from state {self.state}")
        if not self.can_restore:
            raise BenchError("Restore is unavailable: safeguards were not created for this update.")
        self._transition(RESTORING)
        try:
            self._restore_apps(on_step, on_progress)
            self._restore_sites(on_step)
            self._finish_restore(on_step)
        except Exception as error:
            self.diagnosis = {"message": f"Restore failed: {error}", "output_excerpt": ""}
            self._transition(RESTORE_FAILED)
            raise

    def _restore_apps(self, on_step: OnStep, on_progress: OnProgress) -> None:
        if not self.restore_checkpoints.get("apps"):
            on_step("restore_apps", "Restoring app revisions")
            for app in self.apps:
                on_progress(f"Restoring {app.name} to {app.sha[:8]}...")
                self.bench.app(app.name).checkout_commit(app.sha)
            self.restore_checkpoints["apps"] = True
            self._save()
        if not self.restore_checkpoints.get("build") and self.apps:
            on_step("restore_build", "Reinstalling dependencies and assets")
            filter_set = set(self.apps_filter) if self.apps_filter else None
            self.bench._reinstall_apps(filter_set, on_progress)
            self.bench._rebuild_assets(filter_set, on_progress)
            self.restore_checkpoints["build"] = True
            self._save()

    def _restore_sites(self, on_step: OnStep) -> None:
        for site in self.sites:
            if site.migration_status == "pending":
                continue
            key = f"site:{site.name}"
            if self.restore_checkpoints.get(key):
                continue
            on_step("restore_db", f"Restoring database for {site.name}")
            self.bench.site(site.name).snapshot.restore(site.touched_tables)
            self.restore_checkpoints[key] = True
            self._save()

    def _finish_restore(self, on_step: OnStep) -> None:
        on_step("restart", "Restarting services")
        self.bench.reload_workers()
        self._restore_maintenance()
        for site in self.sites:
            if site.snapshot_status == "created":
                self.bench.site(site.name).snapshot.discard()
        self._transition(RESTORED)

    def site(self, name: str) -> SiteProgress:
        for site in self.sites:
            if site.name == name:
                return site
        raise BenchError(f"Site {name!r} is not part of migration {self.id}")

    def _migrate_pending(self, on_step: OnStep, on_progress: OnProgress) -> None:
        self._transition(MIGRATING)
        for site in self.sites:
            if site.migration_status == "success":
                continue
            on_step("migrate", f"Migrating {site.name}")
            on_progress(f"Migrating {site.name}...")
            if not self._migrate_site(site):
                return
        self._complete(on_step)

    def _migrate_site(self, site: SiteProgress) -> bool:
        site.migration_status = "running"
        self._save()
        try:
            self.bench.site(site.name).migrate()
            site.migration_status = "success"
            return True
        except MigrateError as error:
            site.migration_status = "failed"
            self.failed_site = site.name
            self.diagnosis = {
                "message": str(error),
                "output_excerpt": (error.output or "")[-4000:],
            }
            self._transition(NEEDS_ATTENTION)
            return False
        finally:
            self._union_touched_tables(site)
            self._save()

    def _prepare_safeguards(self, on_progress: OnProgress) -> bool:
        """Maintenance-mode every site, then snapshot each unless opted out.

        Returns False (and leaves the operation in needs_attention) if a snapshot
        fails, after restoring the original maintenance settings.
        """
        from pilot.core.site.migration_snapshot import SnapshotUnsupported

        for site in self.sites:
            site_obj = self.bench.site(site.name)
            site.original_config = {"maintenance_mode": 1 if site_obj.maintenance_mode else 0}
            site_obj.set_maintenance_mode(True)
        self._save()

        if self.safeguards_disabled:
            return True

        for site in self.sites:
            try:
                on_progress(f"Creating recovery snapshot for {site.name}...")
                site.previous_tables = self.bench.site(site.name).snapshot.create()
                site.snapshot_status = "created"
                self._save()
            except SnapshotUnsupported as error:
                site.snapshot_status = "unsupported"
                self.safeguards_disabled = True
                on_progress(str(error))
                self._save()
            except Exception as error:
                site.snapshot_status = "failed"
                self.failed_site = site.name
                self.diagnosis = {
                    "message": f"Safeguard failed for {site.name}: {error}",
                    "output_excerpt": "",
                }
                self._restore_maintenance()
                self._transition(NEEDS_ATTENTION)
                return False
        return True

    def _restore_maintenance(self) -> None:
        for site in self.sites:
            original = bool(site.original_config.get("maintenance_mode"))
            self.bench.site(site.name).set_maintenance_mode(original)

    def _complete(self, on_step: OnStep) -> None:
        on_step("restart", "Restarting services")
        self.bench.reload_workers()
        self._restore_maintenance()
        for site in self.sites:
            if site.snapshot_status == "created":
                self.bench.site(site.name).snapshot.discard()
        self._transition(COMPLETED)

    def _union_touched_tables(self, site: SiteProgress) -> None:
        path = self.bench.site(site.name).path / "touched_tables.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(data, list):
            site.touched_tables = sorted(set(site.touched_tables) | {str(t) for t in data})

    def _transition(self, target: str) -> None:
        validate_transition(self.state, target)
        self.state = target
        if target in (UPDATING, MIGRATING) and self.started_at is None:
            self.started_at = _now()
        if target in TERMINAL_STATES:
            self.finished_at = _now()
        self._save()

    def _save(self) -> None:
        self.store.save(self)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "kind": self.kind,
            "state": self.state,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "apps": [vars(app) for app in self.apps],
            "apps_filter": self.apps_filter,
            "sites": [vars(site) for site in self.sites],
            "failed_site": self.failed_site,
            "diagnosis": self.diagnosis,
            "safeguards_disabled": self.safeguards_disabled,
            "skip_failing_patches": self.skip_failing_patches,
            "root_task_id": self.root_task_id,
            "task_ids": self.task_ids,
            "restore_checkpoints": self.restore_checkpoints,
        }

    @classmethod
    def from_dict(cls, data: dict, bench: "Bench", store: "MigrationStore") -> "MigrationOperation":
        operation = cls(
            id=data["id"],
            kind=data["kind"],
            state=data["state"],
            created_at=data["created_at"],
            started_at=data.get("started_at"),
            finished_at=data.get("finished_at"),
            apps=[AppRevision(**app) for app in data.get("apps", [])],
            apps_filter=data.get("apps_filter"),
            sites=[SiteProgress(**site) for site in data.get("sites", [])],
            failed_site=data.get("failed_site"),
            diagnosis=data.get("diagnosis"),
            safeguards_disabled=data.get("safeguards_disabled", False),
            skip_failing_patches=data.get("skip_failing_patches", False),
            root_task_id=data.get("root_task_id"),
            task_ids=data.get("task_ids", {}),
            restore_checkpoints=data.get("restore_checkpoints", {}),
        )
        operation.bench = bench
        operation.store = store
        return operation


class MigrationStore:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self.root = bench.path / "migrations"

    def create_update(
        self, apps_filter: set | None = None, skip_failing_patches: bool = False
    ) -> MigrationOperation:
        selected = [
            app
            for app in self.bench.apps()
            if apps_filter is None or app.config.name in apps_filter
        ]
        apps = [AppRevision(app.config.name, app.installed_hash) for app in selected]
        sites = [site.config.name for site in self.bench.sites()]
        return self._create(
            "update",
            apps=apps,
            apps_filter=sorted(apps_filter) if apps_filter else None,
            sites=sites,
            skip_failing_patches=skip_failing_patches,
        )

    def create_site_migrate(self, site: str) -> MigrationOperation:
        return self._create("site_migrate", apps=[], apps_filter=None, sites=[site])

    def save(self, operation: MigrationOperation) -> None:
        make_private_directory(self.root, parents=True)
        atomic_write_private_text(
            self.root / f"{operation.id}.json",
            json.dumps(operation.to_dict(), indent=2),
        )

    def get(self, operation_id: str) -> MigrationOperation:
        path = self.root / f"{operation_id}.json"
        if not path.exists():
            raise BenchError(f"Migration operation not found: {operation_id}")
        return MigrationOperation.from_dict(json.loads(path.read_text(encoding="utf-8")), self.bench, self)

    def list(self) -> list[MigrationOperation]:
        if not self.root.exists():
            return []
        operations = []
        for path in self.root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                operations.append(MigrationOperation.from_dict(data, self.bench, self))
            except (OSError, ValueError, KeyError):
                continue
        operations.sort(key=lambda operation: operation.id, reverse=True)
        return operations

    def current(self) -> MigrationOperation | None:
        for operation in self.list():
            if not operation.is_resolved:
                return operation
        return None

    def _create(
        self,
        kind: str,
        *,
        apps: list[AppRevision],
        apps_filter: list[str] | None,
        sites: list[str],
        skip_failing_patches: bool = False,
    ) -> MigrationOperation:
        from pilot.core.bench.migration.state import PREPARING

        operation = MigrationOperation(
            id=generate_operation_id(),
            kind=kind,
            state=PREPARING,
            created_at=_now(),
            started_at=None,
            finished_at=None,
            apps=apps,
            apps_filter=apps_filter,
            sites=[SiteProgress(name=name) for name in sites],
            skip_failing_patches=skip_failing_patches,
        )
        operation.bench = self.bench
        operation.store = self
        operation._save()
        return operation
