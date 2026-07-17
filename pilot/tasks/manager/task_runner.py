from __future__ import annotations

import hashlib
import json
import logging
import secrets
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

from pilot.tasks.callbacks import validate_callback
from pilot.tasks.jobs.backup_site_task import BackupSiteTask
from pilot.tasks.jobs.base_task import BaseTask
from pilot.tasks.jobs.build_task import BuildTask
from pilot.tasks.jobs.clear_cache_task import ClearCacheTask
from pilot.tasks.jobs.delete_backup_task import DeleteBackupTask
from pilot.tasks.jobs.drop_site_task import DropSiteTask
from pilot.tasks.jobs.fetch_app_updates_task import FetchAppUpdatesTask
from pilot.tasks.jobs.get_and_install_app_task import GetAndInstallAppTask
from pilot.tasks.jobs.get_app_task import GetAppTask
from pilot.tasks.jobs.install_app_task import InstallAppTask
from pilot.tasks.jobs.migrate_task import MigrateTask
from pilot.tasks.jobs.new_site_from_backup_task import NewSiteFromBackupTask
from pilot.tasks.jobs.new_site_task import NewSiteTask
from pilot.tasks.jobs.reinstall_site_task import ReinstallSiteTask
from pilot.tasks.jobs.remove_app_task import RemoveAppTask
from pilot.tasks.jobs.setup_letsencrypt_task import SetupLetsEncryptTask
from pilot.tasks.jobs.setup_nginx_task import SetupNginxTask
from pilot.tasks.jobs.setup_production_task import SetupProductionTask
from pilot.tasks.jobs.switch_branch_task import SwitchBranchTask
from pilot.tasks.jobs.uninstall_app_task import UninstallAppTask
from pilot.tasks.jobs.update_cli_task import UpdateCliTask
from pilot.tasks.jobs.update_task import UpdateTask
from pilot.tasks.jobs.wizard_setup_task import WizardSetupTask
from pilot.tasks.manager.task_args import (
    fingerprint_task_args,
    public_task_args,
    reject_url_credentials,
    task_secret_args,
)
from pilot.tasks.manager.task_state import (
    TaskStatus,
)
from pilot.tasks.manager.task_process import TaskProcess
from pilot.tasks.manager.task_store import TaskStore
from pilot.tasks.manager.worker_registry import task_workers
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

# command -> job class. Each class's own _parser() is the single source of
# truth for its CLI shape; argv is derived from it below instead of a second,
# hand-synced builder per command.
_JOBS: dict[str, type[BaseTask]] = {
    "migrate": MigrateTask,
    "clear-cache": ClearCacheTask,
    "install-app": InstallAppTask,
    "uninstall-app": UninstallAppTask,
    "get-app": GetAppTask,
    "remove-app": RemoveAppTask,
    "new-site": NewSiteTask,
    "drop-site": DropSiteTask,
    "backup-site": BackupSiteTask,
    "delete-backup": DeleteBackupTask,
    "build": BuildTask,
    "update": UpdateTask,
    "get-and-install-app": GetAndInstallAppTask,
    "switch-branch": SwitchBranchTask,
    "setup-nginx": SetupNginxTask,
    "setup-production": SetupProductionTask,
    "setup-letsencrypt": SetupLetsEncryptTask,
    "new-site-from-backup": NewSiteFromBackupTask,
    "reinstall-site": ReinstallSiteTask,
    "wizard-setup": WizardSetupTask,
    "update-cli": UpdateCliTask,
    "fetch-all-app-updates": FetchAppUpdatesTask,
}


def _argv_suffix(command: str, args: dict) -> list[str]:
    """Build a job's argv (after bench_root) from its own _parser() actions."""
    if command == "get-and-install-app" and "sites" not in args and args.get("site"):
        args = {**args, "sites": [args["site"]]}

    argv: list[str] = []
    for action in _JOBS[command]._parser()._actions:
        if action.dest in ("help", "bench_root") or action.dest not in args:
            continue
        value = args[action.dest]
        if not action.option_strings:
            argv += value if isinstance(value, list) else [str(value)]
        elif action.nargs == 0:
            if value:
                argv.append(action.option_strings[0])
        elif value:
            argv.append(action.option_strings[0])
            argv += value if isinstance(value, list) else [str(value)]
    return argv


class TaskCallback(TypedDict):
    operation: str
    args: dict


class TaskCallbacks(TypedDict, total=False):
    on_success: TaskCallback | None
    on_failure: TaskCallback | None
    on_cancel: TaskCallback | None


@dataclass(frozen=True)
class TaskSubmission:
    task_id: str
    created: bool


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
        resource_key: str | None = None,
    ) -> str:
        return self.submit(
            command,
            args,
            callbacks=callbacks,
            idempotency_key=idempotency_key,
            resource_key=resource_key,
        ).task_id

    def submit(
        self,
        command: str,
        args: dict,
        callbacks: TaskCallbacks | None = None,
        idempotency_key: str | None = None,
        resource_key: str | None = None,
    ) -> TaskSubmission:
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
            "args": public_task_args(command, args),
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
            self._store.create_queued(
                meta,
                private_files,
                resource_key=resource_key,
            )
            submission = TaskSubmission(task_id, True)
        else:
            idempotency_digest = self._idempotency_digest(idempotency_key)
            request_fingerprint = self._request_fingerprint(command, args)
            creation = self._store.create_idempotent_queued(
                meta,
                private_files,
                idempotency_digest,
                request_fingerprint,
                resource_key=resource_key,
            )
            if not creation.created:
                return TaskSubmission(creation.task_id, False)
            submission = TaskSubmission(creation.task_id, True)
        self._run_post_submission_housekeeping()
        return submission

    def _run_post_submission_housekeeping(self) -> None:
        for operation in (
            lambda: task_workers.wake(self._bench_root),
            lambda: self._store.purge_terminal(TASK_RETENTION_LIMIT),
        ):
            try:
                operation()
            except Exception as exc:
                logging.debug("Post-submission housekeeping step failed: %s", exc)

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
            self._store.remove_private_files(task_id, "secrets.json")
            try:
                task_workers.wake(self._bench_root)
            except Exception as exc:
                logging.debug("Failed to wake task workers after kill: %s", exc)
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

        module = _JOBS[command].__module__
        return [sys.executable, "-m", module, str(self._bench_root), *_argv_suffix(command, args)]

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
            {"command": command, "args": fingerprint_task_args(command, args)},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(request.encode()).hexdigest()
