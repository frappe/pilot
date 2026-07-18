from __future__ import annotations

import json
from pathlib import Path

from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked

_DB_SOCKET_CANDIDATES = [
    "/var/run/mysqld/mysqld.sock",
    "/run/mysqld/mysqld.sock",
    "/tmp/mysql.sock",
    "/usr/local/var/mysql/mysql.sock",
]


def list_installed_apps(site_config: dict, bench_root: Path, site_name: str) -> list[str]:
    if isinstance(site_config.get("installed_apps"), list):
        return site_config["installed_apps"]
    apps = query_installed_apps_via_db(site_config)
    if apps is not None:
        return apps
    return query_installed_apps_via_frappe(bench_root, site_name)


def query_installed_apps_via_db(site_config: dict) -> list[str] | None:
    import shutil
    import subprocess

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
        socket_path = next(
            (socket for socket in _DB_SOCKET_CANDIDATES if Path(socket).exists()), None
        )
        if socket_path:
            conn_args.append(f"--socket={socket_path}")
        else:
            conn_args += ["--host=127.0.0.1", f"--port={db_port}"]
    else:
        conn_args += [f"--host={db_host}", f"--port={db_port}"]

    try:
        result = subprocess.run(
            [
                cli,
                *conn_args,
                "--batch",
                "--skip-column-names",
                db_name,
                "-e",
                "SELECT app_name FROM `tabInstalled Application` ORDER BY idx",
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


def set_site_ssl_flag(sites_root: Path, site_name: str, enabled: bool) -> None:
    config_path = safe_site_config_path(sites_root, site_name)
    with exclusive_file_lock(config_path):
        config = json.loads(config_path.read_text())
        config["ssl"] = enabled
        replace_private_text_locked(config_path, json.dumps(config, indent=1))


def safe_site_config_path(sites_root: Path, site_name: str) -> Path:
    if sites_root.is_symlink():
        raise BenchError("Site configuration path must stay within the bench.")
    resolved_root = sites_root.resolve()
    site_path = resolved_root / site_name
    config_path = site_path / "site_config.json"
    if (
        site_path.is_symlink()
        or site_path.resolve(strict=False).parent != resolved_root
        or config_path.is_symlink()
        or not config_path.is_file()
    ):
        raise BenchError("Site configuration is unavailable.")
    return config_path


def query_installed_apps_via_frappe(bench_root: Path, site_name: str) -> list[str]:
    import os
    import subprocess

    python = str(bench_root / "env" / "bin" / "python")
    sites_dir = str(bench_root / "sites")
    try:
        env = os.environ.copy()
        env.pop("PYTHONPATH", None)
        result = subprocess.run(
            [
                python,
                "-m",
                "frappe.utils.bench_helper",
                "frappe",
                "--site",
                site_name,
                "list-apps",
            ],
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
