"""Synthetic bench fixture for benchmarks.

Builds an isolated bench under a temp directory with real git apps, site
configs, tasks, logs, and an audit trail. Nothing here touches a database,
network, or host service, so the harness runs offline and side-effect free.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

from pilot.config import AppConfig, BenchConfig
from pilot.core.bench import Bench
from pilot.internal.tasks.models import TaskStatus
from pilot.internal.tasks.runner import generate_task_id
from pilot.internal.tasks.store import TaskStore

APP_MODULE_TEMPLATE = """__version__ = "{version}"

{body}
"""

HOOKS_TEMPLATE = """app_name = "{name}"
app_title = "{title}"
app_publisher = "Benchmark"
app_description = "Synthetic app for Pilot benchmarks"
app_email = "bench@example.com"
app_license = "MIT"
app_version = "{version}"
required_apps = {required_apps}
"""

CORE_TEMPLATE = '''"""Synthetic app code with enough imports for the validator's AST walks."""


def compute(value: int) -> int:
    return value * 2


class Client:
    def __init__(self, name: str) -> None:
        self.name = name

    def greet(self) -> str:
        return f"Hello {{self.name}}"
'''

PYPROJECT_TEMPLATE = """[project]
name = "{name}"
version = "{version}"
description = "Synthetic app for Pilot benchmarks"
dependencies = []

[build-system]
requires = ["setuptools>=61.0"]
build-backend = "setuptools.build_meta"

[tool.bench.frappe-dependencies]
frappe = ">=15"
"""


@dataclass
class Fixture:
    """A built synthetic bench plus cheap reset helpers."""

    root: Path
    benches_root: Path
    bench_root: Path
    app_names: list[str] = field(default_factory=list)
    site_names: list[str] = field(default_factory=list)
    task_count: int = 0
    log_lines: int = 0
    _pristine: Path = field(default=Path())

    @property
    def config_path(self) -> Path:
        return self.bench_root / "bench.toml"

    def bench(self) -> Bench:
        return Bench(self.bench_root)

    def config(self) -> BenchConfig:
        return BenchConfig.read(self.bench_root)

    def reset(self) -> None:
        """Restore bench.toml, common_config.toml, sites/ and tasks/."""
        shutil.copyfile(self._pristine / "bench.toml", self.config_path)
        common = self.benches_root / "common_config.toml"
        if (self._pristine / "common_config.toml").exists():
            shutil.copyfile(self._pristine / "common_config.toml", common)
        elif common.exists():
            common.unlink()
        self._restore_tree(self._pristine / "sites", self.bench_root / "sites")
        self._restore_tree(self._pristine / "tasks", self.bench_root / "tasks")
        self._restore_tree(self._pristine / "logs", self.bench_root / "logs")
        for name in (".active-jtis.json", ".revoked-jtis.json"):
            (self.bench_root / name).unlink(missing_ok=True)

    def _restore_tree(self, source: Path, target: Path) -> None:
        if target.exists():
            shutil.rmtree(target)
        if source.exists():
            shutil.copytree(source, target)

    def snapshot(self) -> None:
        pristine = self.root / ".pristine"
        pristine.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self.config_path, pristine / "bench.toml")
        common = self.benches_root / "common_config.toml"
        if common.exists():
            shutil.copyfile(common, pristine / "common_config.toml")
        self._copy_tree(self.bench_root / "sites", pristine / "sites")
        self._copy_tree(self.bench_root / "tasks", pristine / "tasks")
        self._copy_tree(self.bench_root / "logs", pristine / "logs")
        self._pristine = pristine

    @staticmethod
    def _copy_tree(source: Path, target: Path) -> None:
        if target.exists():
            shutil.rmtree(target)
        if source.exists():
            shutil.copytree(source, target)


def _run(argv: list[str], cwd: Path) -> None:
    subprocess.run(argv, cwd=cwd, check=True, capture_output=True)


def _write_app_repo(path: Path, remote: Path, name: str, version: str) -> None:
    module = name.replace("-", "_")
    path.mkdir(parents=True, exist_ok=True)
    (path / "pyproject.toml").write_text(PYPROJECT_TEMPLATE.format(name=name, version=version))
    (path / "README.md").write_text(f"# {name}\n\nSynthetic app for Pilot benchmarks.\n")
    package = path / module
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text(APP_MODULE_TEMPLATE.format(version=version, body=""))
    (package / "hooks.py").write_text(
        HOOKS_TEMPLATE.format(name=name, title=name.title(), version=version, required_apps="[]")
    )
    (package / "core.py").write_text(CORE_TEMPLATE)
    (package / "queries.py").write_text(CORE_TEMPLATE)
    _run(["git", "init", "-q", "-b", "main"], path)
    _run(["git", "add", "."], path)
    _run(
        [
            "git",
            "-c",
            "user.email=benchmark@example.com",
            "-c",
            "user.name=Benchmark",
            "commit",
            "-q",
            "-m",
            "initial",
        ],
        path,
    )
    _run(["git", "remote", "add", "origin", str(remote)], path)


def _write_site_config(path: Path, name: str, apps: list[str]) -> None:
    path.mkdir(parents=True, exist_ok=True)
    config = {
        "db_name": f"_bd_{name.replace('.', '_')}",
        "db_type": "sqlite",
        "db_user": "benchmark",
        "db_password": "benchmark",
        "host_name": f"https://{name}",
        "installed_apps": apps,
        "domains": [
            {"domain": name},
            {"domain": f"www.{name}"},
        ],
        "ssl": False,
        "cert_name": "",
        "maintenance_mode": 0,
        "pause_scheduler": 0,
        "backup_retention": {
            "scheme": "calendar",
            "keep_daily": 7,
            "keep_weekly": 4,
            "keep_monthly": 3,
            "keep_yearly": 1,
        },
    }
    (path / "site_config.json").write_text(json.dumps(config, indent=2))
    (path / "logs").mkdir(exist_ok=True)
    (path / "private").mkdir(exist_ok=True)
    (path / "public").mkdir(exist_ok=True)


def _seed_site_database(bench_root: Path, site_name: str, apps: list[str]) -> None:
    """A minimal SQLite site database so DB probes take their real query path."""
    import sqlite3

    site_config = json.loads((bench_root / "sites" / site_name / "site_config.json").read_text())
    db_dir = bench_root / "sites" / site_name / "db"
    db_dir.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_dir / f"{site_config.get('db_name', site_name)}.db")
    try:
        connection.execute("CREATE TABLE tabDefaultValue (parent TEXT, defkey TEXT, defvalue TEXT)")
        connection.execute("INSERT INTO tabDefaultValue VALUES ('__global', 'disabled_apps', '[]')")
        connection.execute(
            "CREATE TABLE 'tabInstalled Application' ("
            "app_name TEXT, idx INTEGER, has_setup_wizard INTEGER, is_setup_complete INTEGER)"
        )
        for index, app in enumerate(apps, start=1):
            connection.execute("INSERT INTO 'tabInstalled Application' VALUES (?, ?, 0, 1)", (app, index))
        connection.execute("CREATE TABLE tabSingles (doctype TEXT, field TEXT, value TEXT)")
        connection.execute("INSERT INTO tabSingles VALUES ('System Settings', 'setup_complete', '1')")
        connection.commit()
    finally:
        connection.close()


def _write_common_site_config(path: Path, apps: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "redis_cache": "redis://localhost:13000",
                "redis_queue": "redis://localhost:11000",
                "socketio_port": 9000,
                "webserver_port": 8000,
                "webserver_host": "127.0.0.1",
                "socketio_backend": "python",
                "monitor": True,
                "workers": {},
                "installed_apps": apps,
            },
            indent=2,
        )
    )


def _seed_tasks(bench_root: Path, count: int, log_lines: int) -> None:
    store = TaskStore(bench_root)
    now = datetime.now(UTC)
    for index in range(count):
        task_id = generate_task_id()
        queued_at = (now - timedelta(minutes=index)).isoformat()
        store.create_queued(
            {
                "task_id": task_id,
                "command": "clear-cache",
                "args": {"site": "site1.localhost"},
                "queued_at": queued_at,
            }
        )
        task_dir = store.task_dir(task_id)
        (task_dir / "output.log").write_text("".join(f"line {line}\n" for line in range(log_lines)))
        if index % 3:
            finished_at = (now - timedelta(minutes=index) + timedelta(seconds=30)).isoformat()
            store.transition(task_id, TaskStatus.QUEUED, TaskStatus.RUNNING, {"started_at": queued_at})
            store.transition(
                task_id,
                TaskStatus.RUNNING,
                TaskStatus.SUCCESS,
                {"finished_at": finished_at, "exit_code": 0},
            )


def _seed_audit(bench_root: Path, entries: int) -> None:
    from pilot.core.bench.audit_log import AuditLog

    log = AuditLog(Bench(bench_root))
    now = datetime.now(UTC)
    for index in range(entries):
        log.append(
            "task",
            {
                "event": "queued",
                "command": "clear-cache",
                "task_id": generate_task_id(),
                "site": "site1.localhost",
                "logged_at": (now - timedelta(minutes=index)).isoformat(),
            },
        )


def _seed_logs(bench_root: Path, lines: int) -> None:
    logs = bench_root / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "web.log").write_text("".join(f"web log line {index}\n" for index in range(lines)))
    (logs / "worker.log").write_text("".join(f"worker log line {index}\n" for index in range(lines)))


def build_fixture(
    root: Path,
    *,
    app_count: int = 3,
    site_count: int = 3,
    task_count: int = 12,
    log_lines: int = 2000,
    audit_entries: int = 300,
) -> Fixture:
    """Build the synthetic bench. `app_count` includes the framework app."""
    root = Path(root)
    root.mkdir(parents=True, exist_ok=True)
    for child in ("benches", "remotes", ".pristine"):
        shutil.rmtree(root / child, ignore_errors=True)
    benches_root = root / "benches"
    bench_root = benches_root / "bench"
    bench_root.mkdir(parents=True)

    remotes = root / "remotes"
    remotes.mkdir()
    app_names = ["frappe"] + [f"app{index}" for index in range(1, app_count)]
    app_names = app_names[:app_count]
    for name in app_names:
        remote = remotes / f"{name}.git"
        _run(["git", "init", "-q", "--bare", str(remote)], root)
        version = "16.0.0" if name == "frappe" else f"1.{name[-1]}.0"
        _write_app_repo(bench_root / "apps" / name, remote, name, version)

    config = BenchConfig.from_flat(
        "bench",
        {
            "admin_enabled": True,
            # No admin domain: `pilot ls` would otherwise ask sudo to test for a
            # certificate, which is a host read the offline harness must avoid.
            "db_type": "sqlite",
            "socketio_backend": "python",
            "watch_apps_js": True,
            "watch_admin_js": False,
            "lite_mode_enabled": False,
            "production_process_manager": "",
        },
        benches_root=benches_root,
    )
    config.admin.jwt_secret = "benchmark-secret-benchmark-secret-1234567890"
    config.apps = [
        AppConfig(name=name, repo=str(remotes / f"{name}.git"), branch="main") for name in app_names
    ]
    config.write(bench_root)

    bench = Bench(bench_root)
    bench.create_directories()

    site_names = [f"site{index}.localhost" for index in range(1, site_count + 1)]
    for site_name in site_names:
        apps = ["frappe", *app_names[1:]]
        _write_site_config(bench_root / "sites" / site_name, site_name, apps)
        _seed_site_database(bench_root, site_name, apps)
    (bench_root / "sites" / "apps.txt").write_text("\n".join(app_names) + "\n")
    _write_common_site_config(bench_root / "sites" / "common_site_config.json", app_names)

    _seed_tasks(bench_root, task_count, min(log_lines, 200))
    _seed_audit(bench_root, audit_entries)
    _seed_logs(bench_root, log_lines)

    fixture = Fixture(
        root=root,
        benches_root=benches_root,
        bench_root=bench_root,
        app_names=app_names,
        site_names=site_names,
        task_count=task_count,
        log_lines=log_lines,
    )
    fixture.snapshot()
    return fixture


__all__ = ["Fixture", "build_fixture"]
