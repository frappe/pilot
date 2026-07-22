from __future__ import annotations

import hashlib
import json
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from pilot.internal.tasks.args import (
    fingerprint_task_args,
    public_task_args,
    reject_url_credentials,
    task_secret_args,
)
from pilot.internal.tasks.authoring import task_argv_suffix
from pilot.internal.tasks.callbacks import validate_callback
from pilot.tasks import Task
from pilot.tasks.callbacks import TaskCallbacks


@dataclass(frozen=True)
class TaskPayload:
    task_id: str
    command: str
    args: dict
    public_args: dict
    secret_args: dict
    command_argv: list[str]
    callbacks: dict
    queued_at: str
    bench_root: Path

    @property
    def metadata(self) -> dict:
        return {
            "task_id": self.task_id,
            "command": self.command,
            "args": self.public_args,
            "command_argv": self.command_argv,
            "queued_at": self.queued_at,
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
            "bench_root": str(self.bench_root),
        }

    @property
    def private_files(self) -> dict[str, str]:
        files = {}
        if self.secret_args:
            files["secrets.json"] = json.dumps(self.secret_args)
        if self.callbacks:
            files["callbacks.json"] = json.dumps(self.callbacks, indent=2)
        return files

    @property
    def request_fingerprint(self) -> str:
        request = json.dumps(
            {"command": self.command, "args": fingerprint_task_args(self.command, self.args)},
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(request.encode()).hexdigest()


class TaskPayloadBuilder:
    def __init__(
        self,
        bench_root: Path,
        jobs: dict[str, type[Task]],
        required_args: dict[str, list[str]],
        generate_task_id: Callable[[], str],
    ) -> None:
        self._bench_root = bench_root
        self._jobs = jobs
        self._required_args = required_args
        self._task_id_generator = generate_task_id

    def build(
        self,
        command: str,
        args: dict,
        callbacks: TaskCallbacks | None,
    ) -> TaskPayload:
        command_argv = self.build_command_argv(command, args)
        return TaskPayload(
            task_id=self._task_id_generator(),
            command=command,
            args=args,
            public_args=public_task_args(command, args),
            secret_args=task_secret_args(command, args),
            command_argv=command_argv,
            callbacks=self.validate_callbacks(callbacks),
            queued_at=datetime.now(UTC).isoformat(),
            bench_root=self._bench_root,
        )

    def build_command_argv(self, command: str, args: dict) -> list[str]:
        self.validate_args(command, args)
        return [
            sys.executable,
            "-m",
            self._jobs[command].__module__,
            str(self._bench_root),
            *task_argv_suffix(self._jobs[command], args),
        ]

    def validate_args(self, command: str, args: dict) -> None:
        if command not in self._required_args:
            raise ValueError(f"Unknown command: {command!r}. Allowed: {sorted(self._required_args)}")
        reject_url_credentials(args)
        for key in self._required_args[command]:
            if key not in args:
                raise ValueError(f"Command {command!r} requires arg {key!r}")
        if "admin_password" in self._required_args[command]:
            password = args["admin_password"]
            if not isinstance(password, str) or not password.strip():
                raise ValueError("admin_password must not be empty")

    @staticmethod
    def validate_callbacks(callbacks: TaskCallbacks | None) -> dict:
        payload = {}
        for trigger, spec in (callbacks or {}).items():
            if trigger not in ("on_success", "on_failure", "on_cancel"):
                raise ValueError(f"Unknown callback trigger: {trigger!r}")
            if spec is not None:
                payload[trigger] = validate_callback(spec)
        return payload
