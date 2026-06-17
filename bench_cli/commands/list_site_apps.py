from __future__ import annotations

import argparse
import json
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.commands.base import Command

if TYPE_CHECKING:
    from bench_cli.core.bench import Bench

_SOCKET_CANDIDATES = [
    "/var/run/mysqld/mysqld.sock",
    "/run/mysqld/mysqld.sock",
    "/tmp/mysql.sock",
    "/usr/local/var/mysql/mysql.sock",
]


def list_installed_apps(site_config: dict, bench_root: Path, site_name: str) -> list[str]:
    """Return installed app names for a site, using the fastest available method."""
    # Fast path: frappe keeps this in sync after install/uninstall (v16+).
    if isinstance(site_config.get("installed_apps"), list):
        return site_config["installed_apps"]
    # Fallback: query DB directly, then frappe subprocess.
    apps = _query_via_db_cli(site_config)
    if apps is not None:
        return apps
    return _query_via_frappe(bench_root, site_name)


def _query_via_db_cli(site_config: dict) -> list[str] | None:
    db_name = site_config.get("db_name", "")
    db_password = site_config.get("db_password", "")
    db_host = site_config.get("db_host") or "localhost"
    db_port = int(site_config.get("db_port") or 3306)
    if not db_name or not db_password:
        return None

    cli = shutil.which("mariadb") or shutil.which("mysql")
    if not cli:
        return None

    conn_args = [f"--user={db_name}", f"--password={db_password}"]
    if db_host in ("localhost", "127.0.0.1", ""):
        socket_path = next((s for s in _SOCKET_CANDIDATES if Path(s).exists()), None)
        if socket_path:
            conn_args.append(f"--socket={socket_path}")
        else:
            conn_args += [f"--host=127.0.0.1", f"--port={db_port}"]
    else:
        conn_args += [f"--host={db_host}", f"--port={db_port}"]

    try:
        result = subprocess.run(
            [
                cli, *conn_args,
                "--batch", "--skip-column-names",
                db_name,
                "-e", "SELECT app_name FROM `tabInstalled Application` ORDER BY idx",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        return [line.strip() for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return None


def _query_via_frappe(bench_root: Path, site_name: str) -> list[str]:
    python = str(bench_root / "env" / "bin" / "python")
    sites_dir = str(bench_root / "sites")
    try:
        import os
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [python, "-m", "frappe.utils.bench_helper", "frappe", "--site", site_name, "list-apps"],
            cwd=sites_dir,
            capture_output=True,
            text=True,
            timeout=15,
            env=env,
        )
        if result.returncode != 0:
            return []
        return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]
    except Exception:
        return []


class ListSiteAppsCommand(Command):
    name = "list-site-apps"
    help = "List apps installed on a site."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("site", help="Site name (e.g. site1.localhost).")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, args.site)

    def __init__(self, bench: "Bench", site_name: str) -> None:
        self.bench = bench
        self.site_name = site_name

    def run(self) -> None:
        from bench_cli.exceptions import BenchError

        site_config_path = self.bench.path / "sites" / self.site_name / "site_config.json"
        if not site_config_path.exists():
            raise BenchError(f"Site '{self.site_name}' does not exist.")

        try:
            site_config = json.loads(site_config_path.read_text())
        except (json.JSONDecodeError, OSError):
            site_config = {}

        apps = list_installed_apps(site_config, self.bench.path, self.site_name)
        for app in apps:
            print(app)
