from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path


class ModuleResolver:
    """Resolves imported modules by stat'ing files under one or more roots,
    instead of importing them, so no module-level code ever runs."""

    _SUFFIXES = (".py", ".so", ".pyd")

    def __init__(self, *roots: Path) -> None:
        self.roots = [root for root in roots if root.is_dir()]

    def unresolved(self, modules: Iterable[str]) -> list[str]:
        """Modules that none of the roots provide."""
        return [module for module in modules if not self._is_resolvable(module)]

    def _is_resolvable(self, module: str) -> bool:
        """Whether the module exists under a root."""
        return any(self._is_under(root, module) for root in self.roots)

    def _is_under(self, root: Path, module: str) -> bool:
        """Whether the module exists under this root."""
        directory = root
        parts = module.split(".")
        for index, part in enumerate(parts):
            if (directory / part).is_dir():
                directory = directory / part
                continue
            return index == len(parts) - 1 and self._is_module_file(directory, part)
        return True

    def _is_module_file(self, directory: Path, name: str) -> bool:
        """Whether the directory has a module file with this name."""
        return any(path.suffix in self._SUFFIXES for path in directory.glob(f"{name}.*"))
