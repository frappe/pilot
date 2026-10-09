from __future__ import annotations

import copy
import json
import secrets
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config import AppConfig
from pilot.core.site.template import SiteTemplate
from pilot.exceptions import BenchError
from pilot.utils import write_private_text

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class BenchFork:
    """Fork a prepared development bench without cloning repositories or installing sites."""

    def __init__(self, source: Bench, name: str, template: Path) -> None:
        self.source = source
        self.name = name
        self.branch = f"agent/{name}-{secrets.token_hex(4)}"
        self.template = SiteTemplate(template)
        self.timings: dict[str, float] = {}

    def run(self, on_progress=print) -> Bench:
        from pilot.core.bench import Bench
        from pilot.core.bench.config_files import BenchConfigFiles
        from pilot.managers.environment import PythonEnvManager

        started = time.monotonic()
        self.validate()
        destination = Bench.create_at(
            self.source.path.parent / self.name,
            self.name,
            db_type=self.source.config.db_type,
            on_progress=on_progress,
        )
        state = {
            "source": str(self.source.path.resolve()),
            "template": str(self.template.path),
            "status": "creating",
            "timings": self.timings,
        }
        self.write_state(destination, state)
        try:
            self.configure(destination)
            destination.create_directories()
            destination.write_common_site_config()
            self.measure("worktrees", lambda: self.create_worktrees(destination), on_progress)
            destination.write_apps_txt()
            environment = PythonEnvManager(destination)
            self.measure("python", lambda: self.install_python(destination, environment), on_progress)
            self.provision(destination, environment, on_progress)
            BenchConfigFiles(destination).write_common_site_config()
            destination.enforce_lite_mode_rules()
            state["status"] = "ready"
        except BaseException:
            state["status"] = "failed"
            on_progress(f"Fork retained at {destination.path} for inspection; its source is unchanged.")
            raise
        finally:
            self.timings["total"] = round(time.monotonic() - started, 3)
            self.write_state(destination, state)
        return destination

    def validate(self) -> None:
        from pilot.config import BenchConfig

        BenchConfig.default(self.name).validate()
        if self.source.config.production.enabled:
            raise BenchError("Fork a development bench, not a production bench.")
        data = self.template.read()
        artifact_source = data.get("artifact_source")
        if artifact_source and Path(artifact_source).resolve() != self.source.path.resolve():
            raise BenchError("Live template artifacts must belong to the source bench.")
        if data["engine"] not in ("mariadb", "postgres") or data["engine"] != self.source.config.db_type:
            raise BenchError("Forks require a matching MariaDB or PostgreSQL development template.")
        self.validate_commits(data["apps"])
        if (self.source.path.parent / self.name).exists():
            raise BenchError(f"Destination bench '{self.name}' already exists.")

    def validate_commits(self, apps: list[dict]) -> None:
        from pilot.internal.git import GitRepo

        for entry in apps:
            app = self.source.app(entry["name"])
            if not GitRepo(app.path).has_commit(entry["commit"]):
                raise BenchError(f"Template commit is unavailable for {entry['name']}.")

    def configure(self, destination: Bench) -> None:
        config = destination.config
        source = self.source.config
        config.python_version = source.python_version
        config.mariadb = copy.deepcopy(source.mariadb)
        config.postgres = copy.deepcopy(source.postgres)
        config.workers = copy.deepcopy(source.workers)
        config.socketio_backend = source.socketio_backend
        config.install_dev_extra = source.install_dev_extra
        config.allow_developer_mode = True
        config.watch_admin_js = False
        config.apps = [
            AppConfig(name=e["name"], repo=e["repo"], branch=self.branch)
            for e in self.template.read()["apps"]
        ]
        config.write(destination.path)

    def create_worktrees(self, destination: Bench) -> None:
        for entry in self.template.read()["apps"]:
            self.source.app(entry["name"]).create_worktree(
                destination.apps_path / entry["name"],
                entry["commit"],
                self.branch,
            )

    def install_python(self, destination: Bench, environment) -> None:
        environment.create_venv()
        for app in destination.apps():
            environment.install_app(app)

    def install_dependencies(self, destination: Bench, environment) -> None:
        from pilot.core.bench.artifacts import BenchArtifacts

        data = self.template.read()
        paths = data.get("artifacts", [])
        if paths:
            root = Path(data.get("artifact_source") or self.template.path / "artifacts")
            BenchArtifacts(root).restore(destination, paths)
        required = {
            f"apps/{app.config.name}/node_modules"
            for app in destination.apps()
            if (app.path / "package.json").exists()
        }
        if not required.issubset(paths):
            environment.install_node_dependencies()

    def provision(self, destination: Bench, environment, on_progress) -> None:
        from pilot.core.bench.cloning.runtime import ForkRuntime

        with ThreadPoolExecutor(max_workers=1) as executor:
            dependencies = executor.submit(
                self.measure,
                "dependencies",
                lambda: self.install_dependencies(destination, environment),
                on_progress,
            )
            with ForkRuntime(destination):
                self.measure(
                    "restore",
                    lambda: self.template.restore_into(destination, f"{self.name}.localhost"),
                    on_progress,
                )
                dependencies.result()
                if "sites/assets" not in self.template.read().get("artifacts", []):
                    self.measure("assets", environment.build_assets, on_progress)

    def measure(self, name: str, action, on_progress) -> None:
        on_progress(f"Fork: {name}")
        started = time.monotonic()
        try:
            action()
        finally:
            self.timings[name] = round(time.monotonic() - started, 3)
            on_progress(f"{name}: {self.timings[name]:.3f}s")

    def write_state(self, destination: Bench, state: dict) -> None:
        write_private_text(destination.path / "fork.json", json.dumps(state, indent=2))
