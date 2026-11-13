from __future__ import annotations

import json
import subprocess
from pathlib import Path


def git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def make_app(bench: Path, name: str, remote: str = "", remote_name: str = "upstream") -> Path:
    path = bench / "apps" / name
    (path / name).mkdir(parents=True)
    (path / name / "hooks.py").write_text("app_name = 'x'\n")
    (path / "node_modules").mkdir()
    git(path, "init", "-q", "-b", "develop")
    git(path, "-c", "user.email=a@b", "-c", "user.name=a", "commit", "-q", "--allow-empty", "-m", "init")
    if remote:
        git(path, "remote", "add", remote_name, remote)
    return path


def make_site(bench: Path, name: str, **config) -> Path:
    path = bench / "sites" / name
    path.mkdir(parents=True)
    (path / "site_config.json").write_text(json.dumps({"db_name": "_db", "db_password": "secret", **config}))
    return path


def make_legacy_bench(tmp_path: Path) -> Path:
    bench = tmp_path / "old-bench"
    make_app(bench, "frappe", "https://github.com/frappe/frappe.git")
    make_app(bench, "erpnext", "https://github.com/frappe/erpnext")
    make_app(bench, "unused")
    (bench / "sites" / "apps.txt").parent.mkdir(parents=True, exist_ok=True)
    (bench / "sites" / "apps.txt").write_text("frappe\nerpnext\n")
    (bench / "sites" / "common_site_config.json").write_text(json.dumps({"db_host": "10.0.0.5"}))
    make_site(bench, "one.local", installed_apps=["frappe", "erpnext", "gone"])
    (bench / "sites" / "assets").mkdir()
    return bench
