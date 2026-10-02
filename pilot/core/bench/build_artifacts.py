from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import tempfile
from pathlib import Path

from pilot.core.bench.artifacts import BenchArtifacts
from pilot.internal.atomic_file import exclusive_file_lock
from pilot.managers.node_dependencies import NodeDependencies
from pilot.utils import get_yarn_bin, run_command


class BuildArtifacts:
    """Reuse completed builds with identical code, environment and toolchains."""

    def __init__(self, bench) -> None:
        self.bench = bench
        self.root = bench.path.parent / ".pilot" / "asset-cache"

    def get_key(self) -> str | None:
        digest = hashlib.sha256(b"pilot-assets-v1\0")
        environment = {
            key: value for key, value in os.environ.items() if key not in {"PWD", "OLDPWD", "SHLVL", "_"}
        }
        digest.update(json.dumps(environment, sort_keys=True).encode())
        config_path = self.bench.sites_path / "common_site_config.json"
        config = json.loads(config_path.read_text()) if config_path.exists() else {}
        digest.update(
            json.dumps({key: config.get(key) for key in ("developer_mode", "esbuild_target")}).encode()
        )
        for executable in ("node", get_yarn_bin(), str(self.bench.python)):
            digest.update(run_command([executable, "--version"]).stdout)
        digest.update(
            run_command(
                [
                    str(self.bench.python),
                    "-c",
                    "import importlib.metadata as m; import json; "
                    "print(json.dumps(sorted((d.metadata['Name'], d.version) for d in m.distributions())))",
                ]
            ).stdout
        )
        for app in self.bench.apps():
            key = self.get_app_key(app)
            if key is None:
                return None
            digest.update(key.encode())
        return digest.hexdigest()

    @staticmethod
    def get_app_key(app) -> str | None:
        if not (app.path / ".git").exists() or BuildArtifacts.has_custom_build_hooks(app):
            return None
        digest = hashlib.sha256(f"{app.config.name}:{app.module_name}".encode() + b"\0")
        digest.update(run_command(["git", "-C", str(app.path), "rev-parse", "HEAD"]).stdout)
        digest.update(run_command(["git", "-C", str(app.path), "branch", "--show-current"]).stdout)
        files = (
            run_command(
                ["git", "-C", str(app.path), "ls-files", "-z", "--cached", "--others", "--exclude-standard"]
            )
            .stdout.decode()
            .split("\0")
        )
        for relative in (".", "frontend", "roster", "ui"):
            files.extend(str(path.relative_to(app.path)) for path in (app.path / relative).glob(".env*"))
            digest.update(NodeDependencies.get_resolved_key(app.path / relative).encode())
        outputs = (f"{app.module_name}/public/dist", "frontend/dist", "roster/dist")
        for name in sorted(set(files) - {""}):
            path = app.path / name
            if "node_modules" in path.parts or any(Path(name).is_relative_to(output) for output in outputs):
                continue
            if path.is_dir():
                return None
            digest.update(name.encode() + b"\0")
            if path.is_symlink():
                digest.update(b"link:" + os.readlink(path).encode())
            if path.is_file():
                digest.update(str(path.stat().st_mode).encode())
                with path.open("rb") as file:
                    digest.update(hashlib.file_digest(file, "sha256").digest())
            digest.update(b"\0")
        return digest.hexdigest()

    @staticmethod
    def has_custom_build_hooks(app) -> bool:
        hooks = app.path / app.module_name / "hooks.py"
        if not hooks.is_file():
            return False
        for node in ast.walk(ast.parse(hooks.read_text())):
            if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.AugAssign)):
                continue
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            names = {target.id for target in targets if isinstance(target, ast.Name)}
            if not names & {"after_build", "after_app_build"}:
                continue
            if (
                app.module_name != "frappe"
                or not isinstance(node.value, ast.Constant)
                or not isinstance(node, ast.Assign)
            ):
                return True
            if node.value.value != "frappe.bundler.build_page_islands":
                return True
        return False

    def paths(self) -> list[str]:
        paths = ["sites/assets"]
        for app in self.bench.apps():
            prefix = f"apps/{app.config.name}"
            paths.extend(
                f"{prefix}/{suffix}"
                for suffix in (f"{app.module_name}/public/dist", "frontend/dist", "roster/dist")
            )
        return [path for path in paths if (self.bench.path / path).is_dir()]

    def capture(self, key: str | None) -> None:
        if key is None:
            return
        paths = self.paths()
        if "sites/assets" not in paths:
            return
        self.root.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(self.root / f"{key}.guard"):
            target = self.root / key
            if target.exists():
                return
            staging = Path(tempfile.mkdtemp(prefix="build-", dir=self.root))
            try:
                for path in paths:
                    BenchArtifacts.copy_directory(self.bench.path / path, staging / path)
                BenchArtifacts.relocate_links(self.bench.path.resolve(), target.resolve(), root=staging)
                (staging / "manifest.json").write_text(json.dumps(paths))
                staging.rename(target)
            finally:
                if staging.exists():
                    shutil.rmtree(staging)

    def restore(self, key: str | None) -> bool:
        if key is None:
            return False
        target = self.root / key
        if not target.is_dir():
            return False
        with exclusive_file_lock(self.root / f"{key}.guard"):
            manifest = target / "manifest.json"
            if not manifest.is_file():
                return False
            paths = json.loads(manifest.read_text())
            if "sites/assets" not in paths or not all((target / path).is_dir() for path in paths):
                return False
            BenchArtifacts(target).restore(self.bench, paths)
        return True
