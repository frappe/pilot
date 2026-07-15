from __future__ import annotations

import hashlib
import json
import secrets
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from admin.backend.tasks.callbacks import run_stored_callback, validate_callback
from admin.backend.tasks.manager.task_args import (
    redact_task_args,
    reject_url_credentials,
    task_secret_args,
)
from admin.backend.tasks.manager.task_state import (
    TaskStatus,
)
from admin.backend.tasks.manager.task_process import TaskProcess
from admin.backend.tasks.manager.task_store import TaskStore
from admin.backend.tasks.manager.worker_registry import task_workers
from pilot.exceptions import TaskNotRunningError

TASK_RETENTION_LIMIT = 100

_WHITELIST: dict[str, list[str]] = {
    "migrate": ["site"],
    "clear-cache": ["site"],
    "install-app": ["site", "app"],
    "uninstall-app": ["site", "app"],
    "get-app": ["name"],
    "remove-app": ["name"],
    "new-site": ["name", "admin_password"],
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
    "new-site-from-backup": ["name", "admin_password", "db_file"],
    "reinstall-site": ["site", "admin_password"],
    "wizard-setup": [],
    "update-cli": [],
    "fetch-all-app-updates": [],
}


class TaskCallback(TypedDict):
    operation: str
    args: dict


class TaskCallbacks(TypedDict, total=False):
    on_success: TaskCallback | None
    on_failure: TaskCallback | None
    on_cancel: TaskCallback | None


class TaskRunner:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root
        self._store = TaskStore(bench_root)
        self._processes = TaskProcess(bench_root)

    def run(
        self,
        command: str,
        args: dict,
        callbacks: TaskCallbacks | None = None,
        idempotency_key: str | None = None,
    ) -> str:
        callback_payload = {}
        for trigger, spec in (callbacks or {}).items():
            if trigger not in ("on_success", "on_failure", "on_cancel"):
                raise ValueError(f"Unknown callback trigger: {trigger!r}")
            if spec is not None:
                callback_payload[trigger] = validate_callback(spec)
        task_id = self._generate_task_id()
        command_argv = self._build_argv(command, args)
        secret_args = task_secret_args(command, args)

        queued_at = datetime.now(timezone.utc).isoformat()
        meta = {
            "task_id": task_id,
            "command": command,
            "args": redact_task_args(args),
            "command_argv": command_argv,
            "queued_at": queued_at,
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
            "bench_root": str(self._bench_root),
        }
        private_files = {}
        if secret_args:
            private_files["secrets.json"] = json.dumps(secret_args)
        if callback_payload:
            private_files["callbacks.json"] = json.dumps(callback_payload, indent=2)
        if idempotency_key is None:
            self._store.create_queued(meta, private_files)
        else:
            idempotency_digest = self._idempotency_digest(idempotency_key)
            request_fingerprint = self._request_fingerprint(command, args)
            creation = self._store.create_idempotent_queued(
                meta,
                private_files,
                idempotency_digest,
                request_fingerprint,
            )
            if not creation.created:
                return creation.task_id
        task_workers.wake(self._bench_root)
        self._store.purge_terminal(TASK_RETENTION_LIMIT)
        return task_id

    def kill(self, task_id: str) -> None:
        status = self._store.read_status(task_id)
        if status not in {TaskStatus.QUEUED, TaskStatus.RUNNING}:
            raise TaskNotRunningError(
                f"Task is not active: {task_id} (status={status.value})"
            )

        if status == TaskStatus.QUEUED:
            if not self._store.transition(
                task_id,
                TaskStatus.QUEUED,
                TaskStatus.KILLED,
                {"finished_at": datetime.now(timezone.utc).isoformat()},
            ):
                current = self._store.read_status(task_id)
                raise TaskNotRunningError(
                    f"Task is not active: {task_id} (status={current.value})"
                )
            try:
                run_stored_callback(self._store.task_dir(task_id), "on_cancel")
            except Exception:
                pass
            self._store.remove_private_files(task_id, "secrets.json", "callbacks.json")
            return
        self._processes.cancel(task_id)

    def _build_argv(self, command: str, args: dict) -> list[str]:
        if command not in _WHITELIST:
            raise ValueError(f"Unknown command: {command!r}. Allowed: {sorted(_WHITELIST)}")
        reject_url_credentials(args)

        required = _WHITELIST[command]
        for key in required:
            if key not in args:
                raise ValueError(f"Command {command!r} requires arg {key!r}")
        if "admin_password" in required:
            password = args["admin_password"]
            if not isinstance(password, str) or not password.strip():
                raise ValueError("admin_password must not be empty")

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
            if args.get("db_type"):
                argv += ["--db-type", args["db_type"]]
            if args.get("apps"):
                argv += ["--apps"] + list(args["apps"])
            return argv
        if command == "drop-site":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.drop_site_task", str(self._bench_root), args["site"]]
        if command == "reinstall-site":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.reinstall_site_task", str(self._bench_root), args["site"]]
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
            if args.get("public_files"):
                argv += ["--public-files", args["public_files"]]
            if args.get("private_files"):
                argv += ["--private-files", args["private_files"]]
            return argv
        if command == "update-cli":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.update_cli_task", str(self._bench_root)]
        if command == "fetch-all-app-updates":
            return [sys.executable, "-m", "admin.backend.tasks.jobs.fetch_app_updates_task", str(self._bench_root)]
        raise ValueError(f"Unhandled command: {command!r}")

    @staticmethod
    def _generate_task_id() -> str:
        return datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + secrets.token_hex(3)

    @staticmethod
    def _idempotency_digest(key: str) -> str:
        if not isinstance(key, str) or not key or len(key) > 255:
            raise ValueError("Idempotency-Key must contain between 1 and 255 characters")
        return hashlib.sha256(key.encode()).hexdigest()

    @staticmethod
    def _request_fingerprint(command: str, args: dict) -> str:
        request = json.dumps(
            {"command": command, "args": redact_task_args(args)},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(request.encode()).hexdigest()
