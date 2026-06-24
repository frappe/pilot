from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, List

from bench_cli.config.bench_config import BenchConfig
from bench_cli.exceptions import BenchError

if TYPE_CHECKING:
    from bench_cli.core.app import App
    from bench_cli.core.site import Site


class Bench:
    def __init__(self, config: BenchConfig, path: Path) -> None:
        self.config = config
        self.path = path

    @property
    def apps_path(self) -> Path:
        return self.path / "apps"

    @property
    def sites_path(self) -> Path:
        return self.path / "sites"

    @property
    def env_path(self) -> Path:
        return self.path / "env"

    @property
    def logs_path(self) -> Path:
        return self.path / "logs"

    @property
    def config_path(self) -> Path:
        return self.path / "config"

    @property
    def pids_path(self) -> Path:
        return self.path / "pids"

    @property
    def python(self) -> Path:
        return self.env_path / "bin" / "python"

    @property
    def frappe_call(self) -> list[str]:
        """Command prefix to invoke frappe's bench helper via the venv Python."""
        return [str(self.python), "-m", "frappe.utils.bench_helper"]

    def app(self, name: str) -> "App":
        """Return an App for a cloned app folder by name or module name.
        Accepts either the folder name or the module name for parity with original bench commands.
        """
        from bench_cli.config.app_config import AppConfig
        from bench_cli.core.app import App

        d = self.apps_path / name
        if not d.is_dir():
            # Try the hyphen variant so module names resolve to their folder.
            d = self.apps_path / name.replace("_", "-")

        if not d.is_dir():
            raise BenchError(f"App {name} not found in bench")

        return App(AppConfig(name=d.name, repo=self._git_remote(d), branch=self._git_branch(d)), self)

    def apps(self) -> List["App"]:
        """Return all cloned apps by scanning apps/ directory."""
        from bench_cli.config.app_config import AppConfig
        from bench_cli.core.app import App

        if not self.apps_path.is_dir():
            return []
        result = []
        for d in sorted(self.apps_path.iterdir()):
            if d.is_dir() and (d / ".git").exists():
                app_config = AppConfig(
                    name=d.name,
                    repo=self._git_remote(d),
                    branch=self._git_branch(d),
                )
                result.append(App(app_config, self))
        return result

    def init_apps(self) -> List["App"]:
        """Return apps declared in bench.toml (used only during bench init)."""
        from bench_cli.core.app import App

        return [App(app_config, self) for app_config in self.config.apps]

    def sites(self) -> List["Site"]:
        """Return all sites by scanning sites/ directory."""
        import json as _json

        from bench_cli.config.site_config import SiteConfig
        from bench_cli.core.site import Site

        if not self.sites_path.is_dir():
            return []
        result = []
        for d in sorted(self.sites_path.iterdir()):
            cfg_path = d / "site_config.json"
            if d.is_dir() and cfg_path.exists():
                try:
                    raw = _json.loads(cfg_path.read_text())
                except Exception:
                    raw = {}
                domains = [
                    (entry.get("domain") if isinstance(entry, dict) else entry)
                    for entry in (raw.get("domains") or [])
                ]
                domains = [d for d in domains if isinstance(d, str) and d]
                host_name = (raw.get("host_name") or "").strip()
                primary = host_name.split("://", 1)[-1] if host_name else ""
                site_config = SiteConfig(name=d.name, apps=[], ssl=bool(raw.get("ssl")), domains=domains, primary_domain=primary)
                result.append(Site(site_config, self))
        return result

    def create_directories(self) -> None:
        for directory in [
            self.apps_path,
            self.sites_path,
            self.sites_path / "assets",
            self.logs_path,
            self.config_path,
            self.pids_path,
        ]:
            directory.mkdir(parents=True, exist_ok=True)

    def write_apps_txt(self) -> None:
        """Write apps.txt from currently cloned apps in apps/ directory."""
        apps_txt = self.sites_path / "apps.txt"
        names = [app.config.name for app in self.apps()]
        apps_txt.write_text("\n".join(names) + "\n" if names else "")

    def set_maintenance_mode(self, enabled: bool) -> None:
        config_path = self.sites_path / "common_site_config.json"
        config = json.loads(config_path.read_text())
        config["maintenance_mode"] = 1 if enabled else 0
        config_path.write_text(json.dumps(config, indent=2))

    def write_common_site_config(self) -> None:
        r = self.config.redis
        redis_cache = f"redis://localhost:{r.cache_port}"
        redis_queue = f"redis://localhost:{r.queue_port}"
        redis_socketio = redis_cache
        config = {
            "redis_cache": redis_cache,
            "redis_queue": redis_queue,
            "redis_socketio": redis_socketio,
            "socketio_port": self.config.socketio_port,
            "webserver_port": self.config.http_port,
            "socketio_backend": self.config.socketio_backend,
        }
        config_path = self.sites_path / "common_site_config.json"
        config_path.write_text(json.dumps(config, indent=2) + "\n")

    def restart(self):
        """Restart bench in case we are running in production"""
        from bench_cli.commands.restart import RestartCommand

        RestartCommand(self).run()

    def reload_workers(self, web_only: bool = False, raises: bool = False):
        from bench_cli.managers.process_manager import ProcessManagerFactory

        try:
            ProcessManagerFactory.create(self).reload_workers(web_only)
        except Exception as e:
            print(f"Failed to reload workers: {e}")
            if raises:
                raise

    @staticmethod
    def _git_remote(path: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(path), "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""

    @staticmethod
    def _git_branch(path: Path) -> str:
        result = subprocess.run(
            ["git", "-C", str(path), "branch", "--show-current"],
            capture_output=True,
            text=True,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
