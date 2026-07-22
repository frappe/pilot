from __future__ import annotations

import json
import secrets
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from pilot.core.bench.migration.operation import AppRevision, MigrationOperation, SiteProgress
from pilot.core.bench.migration.state import get_state
from pilot.exceptions import BenchError, MigrationConflictError, MigrationNotFoundError
from pilot.internal.atomic_file import atomic_write_private_text
from pilot.utils import make_private_directory

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class MigrationStore:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self.root = bench.path / "migrations"

    def create_update(self, apps_filter: set[str] | None = None) -> MigrationOperation:
        from pilot.integrations.marketplace import Marketplace

        marketplace_by_name = {entry["name"]: entry for entry in Marketplace.registry()}
        selected = [
            app for app in self.bench.apps() if apps_filter is None or app.config.name in apps_filter
        ]
        apps = [self._revision(app, marketplace_by_name.get(app.config.name)) for app in selected]
        sites = self._sites_for_apps({app.config.name for app in selected})
        return self._create(
            "update",
            apps=apps,
            apps_filter=sorted(apps_filter) if apps_filter else None,
            sites=sites,
        )

    def _sites_for_apps(self, names: set[str]) -> list[str]:
        """Sites where at least one of `names` is installed, plus any site whose installed-apps lookup came back empty (every real site has 'frappe', so empty means the lookup failed, not that nothing applies)."""
        result = []
        for site in self.bench.sites():
            installed = set(site.installed_apps())
            if not installed or names & installed:
                result.append(site.config.name)
        return result

    def _revision(self, app, marketplace_entry: dict | None) -> AppRevision:
        """Snapshot an app's current + target revision so the update deploys that
        exact revision instead of whatever the marketplace/branch tip is by the
        time the update phase actually runs."""
        pin = app.update_target(marketplace_entry)
        return AppRevision(
            app.config.name,
            app.installed_hash,
            repository_url=app.config.repo,
            target_sha=pin.ref if pin else None,
            target_kind=pin.kind if pin else "commit",
        )

    def create_site_migrate(self, site: str) -> MigrationOperation:
        return self._create("site_migrate", apps=[], apps_filter=None, sites=[site])

    def save(self, operation: MigrationOperation) -> None:
        make_private_directory(self.root, parents=True)
        atomic_write_private_text(
            self.root / f"{operation.id}.json",
            json.dumps(operation.to_dict(), indent=2),
        )

    def delete(self, operation_id: str) -> None:
        (self.root / f"{operation_id}.json").unlink(missing_ok=True)

    def get(self, operation_id: str) -> MigrationOperation:
        path = self.root / f"{operation_id}.json"
        if not path.exists():
            raise MigrationNotFoundError(f"Migration operation not found: {operation_id}")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return MigrationOperation.from_dict(data, self.bench, self)
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise BenchError(f"Could not load migration operation {path.name}: {error}") from error

    def get_all(self) -> list[MigrationOperation]:
        if not self.root.exists():
            return []
        operations = [self.get(path.stem) for path in self.root.glob("*.json")]
        operations.sort(key=lambda operation: operation.id, reverse=True)
        return operations

    def current(self) -> MigrationOperation | None:
        """The unresolved operation to surface globally; failures outrank active runs."""
        unresolved = [operation for operation in self.get_all() if not operation.is_resolved]
        failed = next((operation for operation in unresolved if operation.state.is_failure), None)
        return failed or next(iter(unresolved), None)

    def unresolved_for_site(self, site_name: str) -> list[MigrationOperation]:
        return [
            operation
            for operation in self.get_all()
            if not operation.is_resolved and any(site.name == site_name for site in operation.sites)
        ]

    def _create(
        self,
        kind: str,
        *,
        apps: list[AppRevision],
        apps_filter: list[str] | None,
        sites: list[str],
    ) -> MigrationOperation:
        current = self.current()
        if current is not None:
            raise MigrationConflictError(
                f"Migration {current.id} is still {current.state} - resolve it before starting another."
            )
        operation = MigrationOperation(
            id=self._new_id(),
            kind=kind,
            state=get_state("preparing"),
            created_at=datetime.now(UTC).isoformat(),
            started_at=None,
            finished_at=None,
            apps=apps,
            apps_filter=apps_filter,
            sites=[SiteProgress(name=name) for name in sites],
        )
        operation.bench = self.bench
        operation.store = self
        operation._save()
        return operation

    @staticmethod
    def _new_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)
