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
    """Copy independently writable dependencies only when install inputs match."""

    @staticmethod
    def get_key(path: Path) -> str:
        digest = hashlib.sha256(platform.platform().encode())
        for name in ("package.json", "yarn.lock", ".yarnrc", ".npmrc"):
            file = path / name
            digest.update(name.encode() + b"\0")
            digest.update(file.read_bytes() if file.is_file() else b"missing")
        for directory in (path.parent, Path.home()):
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

    @staticmethod
    def has_matching_install(path: Path, key: str) -> bool:
        stamp = path / "node_modules" / ".pilot-install-key"
        return (path / "node_modules" / ".yarn-integrity").is_file() and (
            stamp.is_file() and stamp.read_text() == key
        )

    @staticmethod
    def get_resolved_key(path: Path) -> str:
        integrity = path / "node_modules/.yarn-integrity"
        if not integrity.is_file():
            return ""
        installed = json.loads(integrity.read_text())
        fields = ("systemParams", "flags", "topLevelPatterns", "lockfileEntries")
        return json.dumps({key: installed.get(key) for key in fields}, sort_keys=True)

    @classmethod
    def copy(cls, source: Path, destination: Path) -> bool:
        if not (source / "yarn.lock").is_file() or not (destination / "yarn.lock").is_file():
            return False
        package = json.loads((destination / "package.json").read_text())
        if package.get("workspaces") or set(package.get("scripts", {})) & {
            "preinstall",
            "install",
            "postinstall",
            "prepare",
        }:
            return False
        dependencies = {
            **package.get("dependencies", {}),
            **package.get("devDependencies", {}),
            **package.get("optionalDependencies", {}),
        }
        if any(str(value).startswith(("file:", "link:", "workspace:")) for value in dependencies.values()):
            return False
        key = cls.get_key(destination)
        if key != cls.get_key(source) or not cls.has_matching_install(source, key):
            return False
        BenchArtifacts.copy_directory(source / "node_modules", destination / "node_modules")
        return True
