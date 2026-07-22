from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from pilot.exceptions import BenchError


class ModuleResolver:
    """Resolves imported modules against a venv's site-packages by stat'ing
    files instead of importing them, so no module-level code ever runs."""

    _SUFFIXES = (".py", ".so", ".pyd")

    def __init__(self, env_path: Path) -> None:
        site_packages = next(env_path.glob("lib/python*/site-packages"), None)
        if site_packages is None:
            raise BenchError(f"No site-packages found in {env_path}.")
        self.site_packages = site_packages

    def unresolved(self, modules: Iterable[str]) -> list[str]:
        """Get modules which are not resolvable in the venv's site-packages."""
        return [module for module in modules if not self._is_resolvable(module)]

    def _is_resolvable(self, module: str) -> bool:
        directory = self.site_packages
        parts = module.split(".")
        for index, part in enumerate(parts):
            if (directory / part).is_dir():
                directory = directory / part
                continue
            return index == len(parts) - 1 and self._is_module_file(directory, part)
        return True

    def _is_module_file(self, directory: Path, name: str) -> bool:
        return any(path.suffix in self._SUFFIXES for path in directory.glob(f"{name}.*"))
