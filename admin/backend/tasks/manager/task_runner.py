from __future__ import annotations

import json
import os
import pickle
import secrets
import signal
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from pilot.exceptions import TaskNotFoundError, TaskNotRunningError

TASK_RETENTION_LIMIT = 100

_WHITELIST: dict[str, list[str]] = {
    "migrate": ["site"],
    "clear-cache": ["site"],
    "install-app": ["site", "app"],
    "uninstall-app": ["site", "app"],
    "get-app": ["name"],
    "remove-app": ["name"],
    "new-site": ["name"],
    "drop-site": ["site"],
    "backup-site": ["site"],
    "delete-backup": ["site", "filenames"],
    "build": [],  # optional: app
    "update": [],
    # Either "site" (single-site flow) or "sites" (bench-wide flow) is required;
    # both callers validate their own shape before calling TaskRunner.run().
    "get-and-install-app": [],
    "switch-branch": ["name", "branch"],
    "setup-nginx": [],
    "setup-production": [],
    "setup-letsencrypt": [],
    "new-site-from-backup": ["name", "db_file"],
    "reinstall-site": ["site", "admin_password"],
    "wizard-setup": [],
    "update-cli": [],
    "fetch-all-app-updates": [],
}


class TaskCallbacks(TypedDict):
    on_success: callable | None
    on_failure: callable | None


class TaskRunner:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def run(self, command: str, args: dict, callbacks: TaskCallbacks | None = None) -> str:
        task_id = self._generate_task_id()
        task_dir = self._task_dir(task_id)
        task_dir.mkdir(parents=True)
        command_argv = self._build_argv(command, args, task_dir)

        meta = {
            "task_id": task_id,
            "command": command,
            "args": args,
            "command_argv": command_argv,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "finished_at": None,
            "exit_code": None,
            "bench_root": str(self._bench_root),
        }
        (task_dir / "meta.json").write_text(json.dumps(meta, indent=2))
        (task_dir / "status").write_text("running")

        if callbacks:
            if on_success := callbacks.get("on_success"):
                (task_dir / "on_success.bin").write_bytes(pickle.dumps(on_success))
            if on_failure := callbacks.get("on_failure"):
                (task_dir / "on_failure.bin").write_bytes(pickle.dumps(on_failure))

        process = subprocess.Popen(
            [sys.executable, "-m", "admin.backend.tasks.manager.wrapper", str(task_dir)],
            start_new_session=True,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        (task_dir / "pid").write_text(str(process.pid))
        self._purge_old_tasks()
        return task_id

    def kill(self, task_id: str) -> None:
        task_dir = self._task_dir(task_id)
        if not task_dir.exists():
            raise TaskNotFoundError(f"Task not found: {task_id}")

        status = (task_dir / "status").read_text().strip()
        if status != "running":
            raise TaskNotRunningError(f"Task is not running: {task_id} (status={status})")

        pid_text = (task_dir / "pid").read_text().strip()
        pid = int(pid_text)
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
        (task_dir / "status").write_text("killed")

    def _task_dir(self, task_id: str) -> Path:
        return self._bench_root / "tasks" / task_id

    def _build_argv(self, command: str, args: dict, task_dir: Path | None = None) -> list[str]:
        if command not in _WHITELIST:
            raise ValueError(f"Unknown command: {command!r}. Allowed: {sorted(_WHITELIST)}")

        required = _WHITELIST[command]
        for key in required:
            if key not in args:
                raise ValueError(f"Command {command!r} requires arg {key!r}")

        if command == "migrate":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.migrate_task", str(self._bench_root), args["site"]]
        if command == "clear-cache":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.clear_cache_task", str(self._bench_root), args["site"]]
        if command == "uninstall-app":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.uninstall_app_task", str(self._bench_root), args["site"], args["app"]]
        if command == "backup-site":
            argv = [sys.executable, "-m", "admin.backend.tasks.jobs.backup_site_task", str(self._bench_root), args["site"]]
            if args.get("with_files"):
                argv += ["--with-files"]
            return argv
        if command == "build":
            argv = [sys.executable, "-m", "admin.backend.tasks.jobs.build_task", str(self._bench_root)]
            if args.get("app"):
                argv += ["--app", args["app"]]
            return argv
        if command == "update":
            argv = [sys.executable, "-m", "admin.backend.tasks.jobs.update_task", str(self._bench_root)]
            if task_dir:
                argv += ["--task-log", str(task_dir / "output.log")]
            if args.get("apps"):
                argv += ["--apps"] + list(args["apps"])
            if args.get("skip_failing_patches"):
                argv += ["--skip-failing-patches"]
            return argv
        if command == "get-app":
            argv = [sys.executable, "-m", "admin.backend.tasks.jobs.get_app_task", str(self._bench_root)]
            if args.get("marketplace_app"):
                argv += ["--marketplace-app", args["marketplace_app"]]
            else:
                argv += ["--repo", args["repo"]]
                if args.get("branch"):
                    argv += ["--branch", args["branch"]]
            return argv
        if command == "remove-app":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.remove_app_task", str(self._bench_root), args["name"]]
        if command == "new-site":
            argv = [sys.executable, "-m", "admin.backend.tasks.jobs.new_site_task", str(self._bench_root), args["name"]]
            if args.get("admin_password"):
                argv += [f"--admin-password={args['admin_password']}"]
            if args.get("db_type"):
                argv += ["--db-type", args["db_type"]]
            return argv
        if command == "drop-site":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.drop_site_task", str(self._bench_root), args["site"]]
        if command == "reinstall-site":
            argv = [sys.executable, "-m", "admin.backend.tasks.jobs.reinstall_site_task", str(self._bench_root), args["site"]]
            argv += [f"--admin-password={args['admin_password']}"]
            return argv
        if command == "delete-backup":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.delete_backup_task", str(self._bench_root), args["site"], *args["filenames"]]
        if command == "install-app":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.install_app_task", str(self._bench_root), args["site"], args["app"]]
        if command == "get-and-install-app":
            argv = [sys.executable, "-m", "admin.backend.tasks.jobs.get_and_install_app_task", str(self._bench_root)]
            if args.get("marketplace_app"):
                argv += ["--marketplace-app", args["marketplace_app"]]
            else:
                argv += ["--repo", args["repo"]]
                if args.get("branch"):
                    argv += ["--branch", args["branch"]]
            sites = args["sites"] if "sites" in args else ([args["site"]] if args.get("site") else [])
            if sites:
                argv += ["--sites", *sites]
            return argv
        if command == "switch-branch":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.switch_branch_task", str(self._bench_root), args["name"], args["branch"]]
        if command == "setup-nginx":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.setup_nginx_task", str(self._bench_root)]
        if command == "setup-production":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.setup_production_task", str(self._bench_root)]
        if command == "setup-letsencrypt":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.setup_letsencrypt_task", str(self._bench_root)]
        if command == "wizard-setup":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.wizard_setup_task", str(self._bench_root)]
        if command == "new-site-from-backup":
            argv = [sys.executable, "-m", "admin.backend.tasks.jobs.new_site_from_backup_task", str(self._bench_root), args["name"], args["db_file"]]
            if args.get("admin_password"):
                argv += [f"--admin-password={args['admin_password']}"]
            if args.get("public_files"):
                argv += ["--public-files", args["public_files"]]
            if args.get("private_files"):
                argv += ["--private-files", args["private_files"]]
            if args.get("convert"):
                argv += ["--force"]
            return argv
        if command == "update-cli":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.update_cli_task", str(self._bench_root)]
        if command == "fetch-all-app-updates":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.fetch_app_updates_task", str(self._bench_root)]
        raise ValueError(f"Unhandled command: {command!r}")

    @staticmethod
    def _generate_task_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)

    def _purge_old_tasks(self) -> None:
        tasks_dir = self._bench_root / "tasks"
        if not tasks_dir.exists():
            return

        completed = [entry for entry in tasks_dir.iterdir() if entry.is_dir() and (entry / "status").exists() and (entry / "status").read_text().strip() != "running"]

        if len(completed) <= TASK_RETENTION_LIMIT:
            return

        completed.sort(key=lambda entry: entry.name)
        to_delete = completed[: len(completed) - TASK_RETENTION_LIMIT]
        for entry in to_delete:
            import shutil

            shutil.rmtree(entry)
