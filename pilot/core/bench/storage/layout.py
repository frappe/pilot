from __future__ import annotations

import typing
from pathlib import Path

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench


class SiteLayout:
    """The parts of a site folder the storage page names."""

    def __init__(self, path: Path) -> None:
        self.path = path

    @property
    def private_files_path(self) -> Path:
        """Files uploaded as private."""
        return self.path / "private" / "files"

    @property
    def public_path(self) -> Path:
        """Public files and assets."""
        return self.path / "public"

    @property
    def backup_dirs(self) -> tuple[Path, Path]:
        """Pilot's backups, and Frappe's own under private/."""
        return self.path / "backups", self.path / "private" / "backups"

    def get_backup_paths(self) -> list[Path]:
        """Every entry in the backup folders."""
        return [child for folder in self.backup_dirs if folder.is_dir() for child in folder.iterdir()]

    def get_other_paths(self) -> list[Path]:
        """Every entry outside private files, public files and backups."""
        paths: list[Path] = []
        for child in self.path.iterdir():
            if child.name in {"public", "backups"}:
                continue
            if child.name == "private":
                paths.extend(entry for entry in child.iterdir() if entry.name not in {"files", "backups"})
                continue
            paths.append(child)
        return paths

    def get_named_paths(self) -> list[Path]:
        """Every path the site's row shows a size for."""
        return [
            self.path,
            self.private_files_path,
            self.public_path,
            *self.backup_dirs,
            *self.get_backup_paths(),
            *self.get_other_paths(),
        ]


class BenchLayout:
    """The parts of a bench folder the storage page names. Anything else is an "other"."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def get_named_paths(self) -> set[Path]:
        """Every path the breakdown shows a size for."""
        bench = self.bench
        named = {bench.env_path, bench.logs_path, *self.get_other_paths()}
        for app in bench.apps():
            named.update([app.path, app.path / ".git"])
        for site in bench.sites():
            named.update(SiteLayout(site.path).get_named_paths())
        return named

    def get_other_paths(self) -> list[Path]:
        """Every entry in the bench, apps and sites folders that no labeled part counts."""
        bench = self.bench
        counted = {
            bench.apps_path,
            bench.sites_path,
            bench.env_path,
            bench.logs_path,
            *(app.path for app in bench.apps()),
            *(site.path for site in bench.sites()),
        }
        folders = [bench.path, bench.apps_path, bench.sites_path]
        return [
            path for folder in folders if folder.is_dir() for path in folder.iterdir() if path not in counted
        ]
