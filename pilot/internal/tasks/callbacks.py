from __future__ import annotations

import json
import logging
import os
import secrets
import shutil
import signal
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar

from pilot.internal.tasks.models import TaskStatus
from pilot.managers.platform import HostsFile

if TYPE_CHECKING:
    from pilot.internal.tasks.process import TaskProcess
    from pilot.internal.tasks.process_identity import ProcessIdentity, ProcessInspector, TaskProcessRecord
    from pilot.internal.tasks.store import TaskStore


class CallbackOperations:
    """The named cleanup steps a task can ask to run after it ends, given the task's meta."""

    triggers: ClassVar[dict[TaskStatus, str]] = {
        TaskStatus.SUCCESS: "on_success",
        TaskStatus.FAILED: "on_failure",
        TaskStatus.KILLED: "on_cancel",
    }

    def __init__(self, meta: dict) -> None:
        self.meta = meta
        self.bench_root = Path(meta["bench_root"])

    @classmethod
    def get_trigger(cls, status: TaskStatus) -> str:
        """The callback trigger a finished task status fires."""
        return cls.triggers[status]

    @classmethod
    def validate(cls, spec: object) -> dict:
        """The spec as {"operation", "args"}; raise ValueError for an unknown or malformed one."""
        if not isinstance(spec, dict):
            raise ValueError("Callback must be a JSON object.")
        operation = spec.get("operation")
        args = spec.get("args", {})
        if not isinstance(operation, str) or operation not in cls.operations:
            raise ValueError(f"Unknown callback operation: {operation!r}")
        if not isinstance(args, dict):
            raise ValueError("Callback args must be a JSON object.")
        try:
            json.dumps(args)
        except (TypeError, ValueError) as exc:
            raise ValueError("Callback args must be JSON serializable.") from exc
        return {"operation": operation, "args": args}

    def run(self, spec: dict) -> None:
        """Validate the spec and run its operation in this process."""
        callback = self.validate(spec)
        self.operations[callback["operation"]](self, callback["args"])

    @property
    def last_task_error(self) -> str:
        """The last nonnumeric error line in the task output, or an empty string."""
        log = self.bench_root / "tasks" / self.meta["task_id"] / "output.log"
        try:
            lines = log.read_text(errors="replace").splitlines()
        except OSError:
            return ""
        for line in reversed(lines):
            error = line.partition(" - - ")[2] if " - - " in line else line
            # The wrapper ends with the exit code, such as "Error: 1".
            if error.startswith("Error: ") and not error[7:].strip().isdigit():
                return error[7:].strip()[:500]
        return ""

    def get_site_path(self, site_name: str) -> Path:
        """The site directory; raise when it would land outside the bench's sites directory."""
        raw_sites_root = self.bench_root / "sites"
        if raw_sites_root.is_symlink():
            raise ValueError("Site callback path must stay within the bench.")
        sites_root = raw_sites_root.resolve()
        site_path = sites_root / site_name
        if site_path.is_symlink() or site_path.resolve(strict=False).parent != sites_root:
            raise ValueError("Site callback path must stay within the bench.")
        return site_path

    def remove_failed_site(self, args: dict) -> None:
        """Drop a site this task half created, if the task still owns its creation."""
        from pilot.core.site.creation import SiteCreationOwner

        site_name = args["site"]
        site_path = self.get_site_path(site_name)
        task_id = self.meta.get("task_id", "")
        owner = SiteCreationOwner(self.bench_root, site_name)
        if not owner.is_owned_by(task_id):
            return
        with owner.locked():
            if not owner.is_owned_by(task_id):
                return
            if not self.drop_failed_site(site_name, site_path):
                raise RuntimeError(
                    f"Could not clean up partial site {site_name!r}; its files were preserved for recovery."
                )
            shutil.rmtree(site_path, ignore_errors=True)
            HostsFile().remove(site_name)
            owner.release(self.meta["task_id"])

    def drop_failed_site(self, site_name: str, site_path: Path) -> bool:
        """Drop the site's database and files without a backup; False when the drop failed."""
        if not (site_path / "site_config.json").is_file():
            return True
        try:
            from pilot.core.bench import Bench
            from pilot.managers.platform import Privileges

            bench = Bench(self.bench_root)
            with Privileges.without_prompts():
                bench.site(site_name).drop(no_backup=True)
            return True
        except Exception as exc:
            logging.debug("Site drop callback failed for %s: %s", site_name, exc)
            return False

    def release_site_creation(self, args: dict) -> None:
        """Give up this task's claim on creating the site."""
        from pilot.core.site.creation import SiteCreationOwner

        with SiteCreationOwner(self.bench_root, args["site"]).locked() as owner:
            owner.release(self.meta["task_id"])

    def disable_site_ssl(self, args: dict) -> None:
        """Turn SSL off in the site's config."""
        from pilot.core.site.config.file import SiteConfigFile

        SiteConfigFile(self.bench_root / "sites", args["site"]).set_values({"ssl": False})

    def cleanup_site_restore(self, args: dict) -> None:
        """Remove a restore's work directories and upload, and the site when asked."""
        bench_root = self.bench_root.resolve()
        if self.meta.get("task_id"):
            for work_dir in (bench_root / "tmp" / "restores").glob(f"{self.meta['task_id']}-*"):
                shutil.rmtree(work_dir, ignore_errors=True)
        if args.get("upload_dir"):
            self.remove_restore_upload(Path(args["upload_dir"]))
        if args.get("remove_site"):
            self.remove_failed_site({"site": args["site"]})

    def remove_restore_upload(self, upload_dir: Path) -> None:
        """Remove one restore upload; raise when it is outside the bench's uploads directory."""
        expected_upload_root = self.bench_root.resolve() / "tmp" / "uploads"
        upload_root = expected_upload_root.resolve()
        if upload_root != expected_upload_root:
            raise ValueError("Restore upload root must stay within the bench.")
        if upload_dir.resolve(strict=False).parent != upload_root or upload_dir.is_symlink():
            raise ValueError("Restore upload path must stay within the bench.")
        shutil.rmtree(upload_dir, ignore_errors=True)

    def reload_workers(self, args: dict) -> None:
        """Reload the bench workers, or only the web workers."""
        from pilot.core.bench import Bench

        Bench(self.bench_root).reload_workers(web_only=args.get("web_only", False), raises=True)

    def fail_migration_step(self, args: dict) -> None:
        """Mark the migration step this task ran as failed, with the task's last error."""
        from pilot.core.bench import Bench

        operation = Bench(self.bench_root).migrations.get(args["operation_id"])
        task_id = self.meta["task_id"]
        if previous_task_id := args.get("previous_task_id"):
            if not operation.chain or operation.chain[-1]["task_id"] != previous_task_id:
                return
            task_id = previous_task_id
        operation.fail_stopped_step(task_id, args["phase"], args.get("site"), self.last_task_error)

    operations: ClassVar[dict[str, Callable[[CallbackOperations, dict], None]]] = {
        "cleanup-site-restore": cleanup_site_restore,
        "remove-failed-site": remove_failed_site,
        "disable-site-ssl": disable_site_ssl,
        "reload-workers": reload_workers,
        "fail-migration-step": fail_migration_step,
        "release-site-creation": release_site_creation,
    }


class BoundedCallback:
    """Runs one callback in a child process with a time limit, so a hung cleanup cannot block the task."""

    timeout = 1200

    def __init__(self, spec: dict, meta: dict, *, recovery: bool = False) -> None:
        self.spec = spec
        self.meta = meta
        self.recovery = recovery

    @classmethod
    def run_stored(cls, task_dir: Path, trigger: str) -> None:
        """Run the callback a task stored for this trigger, if any, as a recovery run."""
        callbacks_path = task_dir / "callbacks.json"
        if not callbacks_path.exists():
            return
        if callback := json.loads(callbacks_path.read_text()).get(trigger):
            cls(callback, json.loads((task_dir / "meta.json").read_text()), recovery=True).run()

    def run(self) -> None:
        """Start the child, wait for it within the time limit, and clean up after it."""
        from pilot.internal.tasks.process_identity import ProcessInspector, TaskProcessRecord
        from pilot.internal.tasks.store import TaskStore

        CallbackOperations.validate(self.spec)
        store = TaskStore(Path(self.meta["bench_root"]))
        argv = [sys.executable, "-m", "pilot.internal.tasks.callbacks"]
        if self.recovery:
            argv.append(str(store.task_dir(self.meta["task_id"])))
        launch_id = (None if self.recovery else os.environ.get("BENCH_TASK_LAUNCH_ID")) or secrets.token_hex(
            16
        )
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
            env={**os.environ, "BENCH_TASK_LAUNCH_ID": launch_id},
        )
        inspector = ProcessInspector()
        identity = None
        try:
            identity = inspector.capture(process.pid, argv, launch_id)
            task_id = str(self.meta.get("task_id", "callback"))
            record = TaskProcessRecord(task_id, argv, identity, is_callback=self.recovery)
            if self.recovery:
                store.write_process(self.meta["task_id"], process.pid, record.to_dict())
            self.wait(process, record)
        finally:
            self.close(process)
            if self.recovery and identity is not None:
                self.clear_stopped(store, self.meta["task_id"], inspector, identity, argv)

    def wait(self, process: subprocess.Popen, record: TaskProcessRecord) -> None:
        """Send the callback to the child and wait; kill it and raise on timeout or failure."""
        from pilot.internal.tasks.process import TaskProcess

        try:
            _, stderr = process.communicate(
                json.dumps({"callback": self.spec, "meta": self.meta}).encode(), timeout=self.timeout
            )
        except subprocess.TimeoutExpired:
            self.terminate(process, record, TaskProcess(Path(self.meta["bench_root"])))
            raise RuntimeError(f"Callback timed out after {self.timeout}s.") from None
        if process.returncode:
            raise RuntimeError(stderr.decode(errors="replace").strip() or "Callback failed.")

    @staticmethod
    def close(process: subprocess.Popen) -> None:
        """Kill the child if it still runs and close its pipes."""
        if process.poll() is None:
            process.kill()
            process.wait(timeout=3)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe:
                pipe.close()

    @staticmethod
    def terminate(process: subprocess.Popen, record: TaskProcessRecord, controller: TaskProcess) -> None:
        """Kill every process the callback started, for up to three seconds."""
        from pilot.internal.tasks.process_identity import ProcessInspector

        inspector = ProcessInspector()
        deadline = time.monotonic() + 3
        parent = {os.getpid()}
        while inspector.owned_pids(record.identity) - parent and time.monotonic() < deadline:
            controller._signal(record, signal.SIGKILL, exclude_pids=parent)
            time.sleep(0.05)
        process.wait(timeout=3)

    @staticmethod
    def clear_stopped(
        store: TaskStore,
        task_id: str,
        inspector: ProcessInspector,
        identity: ProcessIdentity,
        argv: list[str],
    ) -> None:
        """Remove the stored process record once the callback and its children are gone."""
        from pilot.internal.tasks.process_identity import ProcessOwnership

        if inspector.inspect(identity, argv) not in {ProcessOwnership.DEAD, ProcessOwnership.STALE}:
            return
        if not inspector.owned_pids(identity):
            store.remove_private_files(task_id, "process.json")


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    CallbackOperations(payload["meta"]).run(payload["callback"])
