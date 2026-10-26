from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked

if TYPE_CHECKING:
    from pathlib import Path

    from pilot.core.bench import Bench

HOLD_SECONDS = 150


class SiteAliases:
    """Old site paths that a rename leaves as symlinks, and when each may go."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    @property
    def registry_path(self) -> "Path":
        return self.bench.config_path / "site-aliases.json"

    def record(self, name: str) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.registry_path):
            renamed = self._read()
            renamed[name] = time.time()
            self._write(renamed)

    def release(self, name: str) -> None:
        """Free a name for a rename or a new site, once its old requests are done."""
        path = self.bench.sites_path / name
        if not path.is_symlink():
            return
        renamed_at = self._read().get(name, path.lstat().st_mtime)
        if time.time() - renamed_at < HOLD_SECONDS:
            raise BenchError(f"Site '{name}' was renamed moments ago. Try again in a few minutes.")
        self._remove(name)

    def release_expired(self) -> None:
        """Remove rename aliases past their hold. Symlinks that a rename did not create stay."""
        expired = [name for name, at in self._read().items() if time.time() - at >= HOLD_SECONDS]
        for name in expired:
            self._remove(name)

    def _remove(self, name: str) -> None:
        path = self.bench.sites_path / name
        if path.is_symlink():
            path.unlink()
        with exclusive_file_lock(self.registry_path):
            renamed = self._read()
            if renamed.pop(name, None) is not None:
                self._write(renamed)

    def _read(self) -> dict[str, float]:
        try:
            return json.loads(self.registry_path.read_text())
        except FileNotFoundError:
            return {}

    def _write(self, renamed: dict[str, float]) -> None:
        """Callers hold the registry lock."""
        replace_private_text_locked(self.registry_path, json.dumps(renamed, indent=2))
