from __future__ import annotations

import json
import logging
import os
import secrets
import time
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.utils import normalize_host

if TYPE_CHECKING:
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
        if not self._remove(name):
            raise BenchError("This site rename is still pending. Retry its task before reusing the name.")

    def release_expired(self) -> None:
        """Remove rename aliases past their hold. Symlinks that a rename did not create stay."""
        expired = [name for name, at in self._read().items() if time.time() - at >= HOLD_SECONDS]
        for name in expired:
            self._remove(name, preserve_hostnames=True)

    def retarget(self, current: str, target: str) -> None:
        """Keep older compatibility paths pointing directly to the current directory."""
        with exclusive_file_lock(self.registry_path):
            for name in self._read():
                path = self.bench.sites_path / name
                if not path.is_symlink() or str(path.readlink()) != current:
                    continue
                temporary = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
                try:
                    temporary.symlink_to(target)
                    os.replace(temporary, path)
                finally:
                    temporary.unlink(missing_ok=True)

    def _remove(self, name: str, *, preserve_hostnames: bool = False) -> bool:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.registry_path):
            if self._is_pending(name):
                return False
            if preserve_hostnames and self.bench.site_claiming(name):
                return False
            path = self.bench.sites_path / name
            if path.is_symlink():
                path.unlink()
            renamed = self._read()
            if renamed.pop(name, None) is not None:
                self._write(renamed)
        return True

    def pending_owner(self, name: str) -> str | None:
        """Resolve either reserved name to the directory of an unfinished rename."""
        for path in (self.bench.config_path / "site-renames").glob("*.json"):
            progress = json.loads(path.read_text())
            if not isinstance(progress, dict):
                raise ValueError(f"Invalid pending rename in {path}")
            move = progress.get("target")
            if not isinstance(move, list) or len(move) != 3:
                raise ValueError(f"Invalid pending rename in {path}")
            target = move[0]
            if not isinstance(target, str) or target in {"", ".", ".."} or Path(target).name != target:
                raise ValueError(f"Invalid pending rename target in {path}")
            if normalize_host(name) not in {normalize_host(path.stem), normalize_host(target)}:
                continue
            destination = self.bench.sites_path / target
            return target if destination.is_dir() and not destination.is_symlink() else path.stem
        return None

    def _is_pending(self, name: str) -> bool:
        try:
            return self.pending_owner(name) is not None
        except (OSError, ValueError, KeyError, TypeError):
            logging.warning("Cannot read pending renames; retaining compatibility paths")
            return True

    def _read(self) -> dict[str, float]:
        try:
            return json.loads(self.registry_path.read_text())
        except FileNotFoundError:
            return {}

    def _write(self, renamed: dict[str, float]) -> None:
        """Callers hold the registry lock."""
        replace_private_text_locked(self.registry_path, json.dumps(renamed, indent=2))
