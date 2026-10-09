from __future__ import annotations

import hashlib
import json
import os
import platform
import shutil
from pathlib import Path

from pilot.core.bench.artifacts import BenchArtifacts
from pilot.utils import get_yarn_bin


class NodeDependencies:
    """Copy reusable dependencies, reconciling changed inputs with Yarn."""

    @staticmethod
    def get_key(path: Path, *, include_dependencies: bool = True) -> str:
        digest = hashlib.sha256(platform.platform().encode())
        files = (
            ("package.json", "yarn.lock", ".yarnrc", ".npmrc")
            if include_dependencies
            else (".yarnrc", ".npmrc")
        )
        for name in files:
            file = path / name
            digest.update(name.encode() + b"\0")
            digest.update(file.read_bytes() if file.is_file() else b"missing")
        for directory in (*path.resolve().parents, Path.home()):
            for name in (".yarnrc", ".npmrc"):
                file = directory / name
                digest.update(file.read_bytes() if file.is_file() else b"missing")
        for binary in (shutil.which("node"), shutil.which(get_yarn_bin())):
            if binary:
                file = Path(binary).resolve()
                digest.update(f"{file}:{file.stat().st_mtime_ns}:{file.stat().st_size}".encode())
        environment = {
            key: value for key, value in os.environ.items() if key not in {"PWD", "OLDPWD", "SHLVL", "_"}
        }
        digest.update(json.dumps(environment, sort_keys=True).encode())
        return digest.hexdigest()

    @classmethod
    def has_matching_install(cls, path: Path, key: str) -> bool:
        stamp = path / "node_modules" / ".pilot-install-key"
        try:
            return stamp.read_text() == key and bool(cls.get_resolved_key(path))
        except (OSError, UnicodeError):
            return False

    @staticmethod
    def get_resolved_key(path: Path) -> str:
        integrity = path / "node_modules/.yarn-integrity"
        try:
            installed = json.loads(integrity.read_text())
        except (OSError, UnicodeError, json.JSONDecodeError):
            return ""
        if not isinstance(installed, dict):
            return ""
        fields = ("systemParams", "flags", "topLevelPatterns", "lockfileEntries")
        return json.dumps({key: installed.get(key) for key in fields}, sort_keys=True)

    @staticmethod
    def is_portable(path: Path) -> bool:
        package = json.loads((path / "package.json").read_text())
        if package.get("workspaces") or set(package.get("scripts", {})) & {
            "preinstall",
            "install",
            "postinstall",
            "prepare",
        }:
            return False
        return not any(
            str(value).startswith(("file:", "link:", "workspace:", "./", "../", "/", "~/"))
            for field in ("dependencies", "devDependencies", "optionalDependencies", "resolutions")
            for value in package.get(field, {}).values()
        )

    @classmethod
    def copy(cls, source: Path, destination: Path) -> bool:
        if not all(
            (path / file).is_file()
            for path in (source, destination)
            for file in ("package.json", "yarn.lock")
        ):
            return False
        if not all(cls.is_portable(path) for path in (source, destination)):
            return False
        source_key, target_key = cls.get_key(source), cls.get_key(destination)
        if not cls.has_matching_install(source, source_key):
            return False
        if cls.get_key(source, include_dependencies=False) != cls.get_key(
            destination, include_dependencies=False
        ):
            return False
        BenchArtifacts.copy_directory(source / "node_modules", destination / "node_modules")
        if source_key != target_key:
            (destination / "node_modules/.pilot-install-key").unlink(missing_ok=True)
        return True
