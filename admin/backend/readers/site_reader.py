from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_SOCKET_CANDIDATES = [
    "/var/run/mysqld/mysqld.sock",
    "/run/mysqld/mysqld.sock",
    "/tmp/mysql.sock",
    "/usr/local/var/mysql/mysql.sock",
]


@dataclass
class SiteInfo:
    name: str
    exists: bool
    db_name: str
    db_host: str
    installed_apps: list[str]
    site_config: dict


class SiteReader:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def read_all(self) -> list[SiteInfo]:
        sites_path = self._bench_root / "sites"
        if not sites_path.is_dir():
            return []
        return [
            self._read_site(d.name)
            for d in sorted(sites_path.iterdir())
            if d.is_dir() and (d / "site_config.json").exists()
        ]

    def read_one(self, site_name: str) -> SiteInfo:
        return self._read_site(site_name)

    def _read_site(self, site_name: str) -> SiteInfo:
        site_config_path = self._bench_root / "sites" / site_name / "site_config.json"
        exists = site_config_path.exists()
        site_config: dict = {}

        if exists:
            try:
                site_config = json.loads(site_config_path.read_text())
            except (json.JSONDecodeError, OSError):
                site_config = {}

        installed_apps = _list_installed_apps(site_config, self._bench_root, site_name) if exists else []

        return SiteInfo(
            name=site_name,
            exists=exists,
            db_name=site_config.get("db_name", ""),
            db_host=site_config.get("db_host", "localhost"),
            installed_apps=installed_apps,
            site_config=site_config,
        )


def _list_installed_apps(site_config: dict, bench_root: Path, site_name: str) -> list[str]:
    apps = _query_via_db_cli(site_config)
    if apps is not None:
        return apps
    return _query_via_frappe(bench_root, site_name)


def _query_via_db_cli(site_config: dict) -> list[str] | None:
    """Query installed apps via mariadb/mysql CLI, with unix socket auto-detection."""
    db_name = site_config.get("db_name", "")
    db_password = site_config.get("db_password", "")
    db_host = site_config.get("db_host", "localhost")
    db_port = int(site_config.get("db_port", 3306))
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
                "-e", "SELECT app_name FROM `tabInstalled Applications` ORDER BY idx",
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
    """Fallback: spawn frappe bench_helper to list installed apps."""
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
