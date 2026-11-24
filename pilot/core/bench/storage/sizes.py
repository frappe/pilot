from __future__ import annotations

import typing
from pathlib import Path

from pilot.core.bench.storage.layout import SiteLayout
from pilot.core.bench.storage.models import AppStorage, SiteStorage, StorageItem
from pilot.internal.disk_scan import DiskScan

if typing.TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.core.site import Site


class FolderSizes:
    """The sizes of the named paths under a root, from one `DiskScan`.

    Memory holds only the named paths and each app's outermost node_modules, not every folder."""

    def __init__(self, root: Path, named: set[Path], apps_path: Path | None = None) -> None:
        self.named = named
        self.apps_path = apps_path
        self.sizes = DiskScan(root).get_sizes(self.keep)

    def keep(self, path: Path) -> bool:
        """Whether the breakdown needs this folder's size."""
        return path in self.named or (path.name == "node_modules" and self.is_outer_node_modules(path))

    def is_outer_node_modules(self, path: Path) -> bool:
        """A node_modules inside an app and not inside another node_modules."""
        if self.apps_path is None or self.apps_path not in path.parents:
            return False
        return "node_modules" not in path.relative_to(self.apps_path).parts[1:-1]

    def get_bytes(self, path: Path) -> int:
        """A folder's size from the scan; a file's or symlink's own allocated bytes."""
        size = self.sizes.get(path)
        return DiskScan.get_entry_bytes(path) if size is None else size

    def get_entry(self, path: Path) -> StorageItem:
        """A file's length or a folder's size, by name."""
        size = path.stat().st_size if path.is_file() else self.get_bytes(path)
        return StorageItem(name=path.name, bytes=size)

    def get_node_modules_bytes(self, app_path: Path) -> int:
        """Every node_modules in the app; a nested one counts once, inside its outer one."""
        return sum(
            size
            for path, size in self.sizes.items()
            if path.name == "node_modules" and app_path in path.parents and self.is_outer_node_modules(path)
        )

    def get_app_storage(self, app: "App") -> AppStorage:
        """One app's row, with its git history and node_modules split out."""
        return AppStorage(
            name=app.config.name,
            bytes=self.get_bytes(app.path),
            git_bytes=self.get_bytes(app.path / ".git"),
            node_modules_bytes=self.get_node_modules_bytes(app.path),
        )

    def get_site_storage(self, site: "Site") -> SiteStorage:
        """One site's row: private files, public files, backups, and the rest."""
        layout = SiteLayout(site.path)
        total_bytes = self.get_bytes(site.path)
        private_files_bytes = self.get_bytes(layout.private_files_path)
        public_files_bytes = self.get_bytes(layout.public_path)
        backups_bytes = sum(self.get_bytes(path) for path in layout.backup_dirs if path.is_dir())
        return SiteStorage(
            name=site.config.name,
            bytes=total_bytes,
            private_files_bytes=private_files_bytes,
            public_files_bytes=public_files_bytes,
            backups_bytes=backups_bytes,
            other_bytes=max(total_bytes - private_files_bytes - public_files_bytes - backups_bytes, 0),
            backup_files=[self.get_entry(path) for path in layout.get_backup_paths()],
            other_entries=[self.get_entry(path) for path in layout.get_other_paths()],
        )
