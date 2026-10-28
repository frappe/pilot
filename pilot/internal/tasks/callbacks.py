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
from typing import TYPE_CHECKING

from pilot.internal.tasks.models import TaskStatus
from pilot.managers.platform import remove_hosts_entry

if TYPE_CHECKING:
    from pilot.internal.tasks.process_identity import TaskProcessRecord

CALLBACK_TIMEOUT_SECONDS = 1200

CallbackOperation = Callable[[dict, dict], None]


def trigger_for_task_status(status: TaskStatus) -> str:
    return {
        TaskStatus.SUCCESS: "on_success",
        TaskStatus.FAILED: "on_failure",
        TaskStatus.KILLED: "on_cancel",
    }[status]


_HOSTS_PATH = Path("/etc/hosts")


def _safe_site_path(bench_root: Path, site_name: str) -> Path:
    raw_sites_root = bench_root / "sites"
    if raw_sites_root.is_symlink():
        raise ValueError("Site callback path must stay within the bench.")
    sites_root = raw_sites_root.resolve()
    site_path = sites_root / site_name
    if site_path.is_symlink() or site_path.resolve(strict=False).parent != sites_root:
        raise ValueError("Site callback path must stay within the bench.")
    return site_path


def _remove_failed_site(meta: dict, args: dict) -> None:
    from pilot.core.site.creation import SiteCreationOwner

    site_name = args["site"]
    bench_root = Path(meta["bench_root"])
    site_path = _safe_site_path(bench_root, site_name)
    owner = SiteCreationOwner(bench_root, site_name)
    if not owner.is_owned_by(meta.get("task_id", "")):
        return
    with owner.locked():
        if not owner.is_owned_by(meta.get("task_id", "")):
            return
        if not _drop_failed_site(bench_root, site_name, site_path):
            raise RuntimeError(
                f"Could not clean up partial site {site_name!r}; its files were preserved for recovery."
            )
        shutil.rmtree(site_path, ignore_errors=True)
        _remove_from_hosts(site_name)
        owner.release(meta["task_id"])


def _release_site_creation(meta: dict, args: dict) -> None:
    from pilot.core.site.creation import SiteCreationOwner

    with SiteCreationOwner(Path(meta["bench_root"]), args["site"]).locked() as owner:
        owner.release(meta["task_id"])


def _drop_failed_site(bench_root: Path, site_name: str, site_path: Path) -> bool:
    if not (site_path / "site_config.json").is_file():
        return True
    try:
        from pilot.core.bench import Bench
        from pilot.managers.platform import noninteractive_privileges

        bench = Bench(bench_root)
        with noninteractive_privileges():
            bench.site(site_name).drop(no_backup=True)
        return True
    except Exception as exc:
        logging.debug("Site drop callback failed for %s: %s", site_name, exc)
        return False


def _remove_from_hosts(site_name: str) -> None:
    remove_hosts_entry(site_name, hosts_path=_HOSTS_PATH)


def _disable_site_ssl(meta: dict, args: dict) -> None:
    from pilot.core.site import set_site_ssl_flag

    sites_root = Path(meta["bench_root"]) / "sites"
    set_site_ssl_flag(sites_root, args["site"], False)


def _cleanup_site_restore(meta: dict, args: dict) -> None:
    bench_root = Path(meta["bench_root"]).resolve()
    if meta.get("task_id"):
        for work_dir in (bench_root / "tmp" / "restores").glob(f"{meta['task_id']}-*"):
            shutil.rmtree(work_dir, ignore_errors=True)
    if args.get("upload_dir"):
        _remove_restore_upload(bench_root, Path(args["upload_dir"]))
    if args.get("remove_site"):
        _remove_failed_site(meta, {"site": args["site"]})


def _remove_restore_upload(bench_root: Path, upload_dir: Path) -> None:
    expected_upload_root = bench_root / "tmp" / "uploads"
    upload_root = expected_upload_root.resolve()
    if upload_root != expected_upload_root:
        raise ValueError("Restore upload root must stay within the bench.")
    resolved_upload_dir = upload_dir.resolve(strict=False)
    if resolved_upload_dir.parent != upload_root or upload_dir.is_symlink():
        raise ValueError("Restore upload path must stay within the bench.")
    shutil.rmtree(upload_dir, ignore_errors=True)


def _reload_workers(meta: dict, args: dict) -> None:
    from pilot.core.bench import Bench

    Bench(Path(meta["bench_root"])).reload_workers(web_only=args.get("web_only", False), raises=True)


def _fail_migration_step(meta: dict, args: dict) -> None:
    from pilot.core.bench import Bench

    operation = Bench(Path(meta["bench_root"])).migrations.get(args["operation_id"])
    task_id = meta["task_id"]
    if previous_task_id := args.get("previous_task_id"):
        if not operation.chain or operation.chain[-1]["task_id"] != previous_task_id:
            return
        task_id = previous_task_id
    operation.fail_stopped_step(task_id, args["phase"], args.get("site"), _last_task_error(meta))


def _last_task_error(meta: dict) -> str:
    """Return the last nonnumeric task error, or an empty string."""
    log = Path(meta["bench_root"]) / "tasks" / meta["task_id"] / "output.log"
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


_OPERATIONS: dict[str, CallbackOperation] = {
    "cleanup-site-restore": _cleanup_site_restore,
    "remove-failed-site": _remove_failed_site,
    "disable-site-ssl": _disable_site_ssl,
    "reload-workers": _reload_workers,
    "fail-migration-step": _fail_migration_step,
    "release-site-creation": _release_site_creation,
}


def validate_callback(spec: object) -> dict:
    if not isinstance(spec, dict):
        raise ValueError("Callback must be a JSON object.")
    operation = spec.get("operation")
    args = spec.get("args", {})
    if not isinstance(operation, str) or operation not in _OPERATIONS:
        raise ValueError(f"Unknown callback operation: {operation!r}")
    if not isinstance(args, dict):
        raise ValueError("Callback args must be a JSON object.")
    try:
        json.dumps(args)
    except (TypeError, ValueError) as exc:
        raise ValueError("Callback args must be JSON serializable.") from exc
    return {"operation": operation, "args": args}


def run_callback(spec: dict, meta: dict) -> None:
    callback = validate_callback(spec)
    _OPERATIONS[callback["operation"]](meta, callback["args"])


def run_bounded_callback(spec: dict, meta: dict, *, recovery: bool = False) -> None:
    from pilot.internal.tasks.process_identity import ProcessInspector, TaskProcessRecord
    from pilot.internal.tasks.store import TaskStore

    validate_callback(spec)
    store = TaskStore(Path(meta["bench_root"]))
    argv = [sys.executable, "-m", "pilot.internal.tasks.callbacks"]
    if recovery:
        argv.append(str(store.task_dir(meta["task_id"])))
    launch_id = (None if recovery else os.environ.get("BENCH_TASK_LAUNCH_ID")) or secrets.token_hex(16)
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
        record = TaskProcessRecord(str(meta.get("task_id", "callback")), argv, identity, is_callback=recovery)
        if recovery:
            store.write_process(meta["task_id"], process.pid, record.to_dict())
        _wait_for_callback(process, record, spec, meta)
    finally:
        _close_callback(process)
        if recovery and identity is not None:
            _clear_stopped_callback(store, meta["task_id"], inspector, identity, argv)


def _wait_for_callback(process, record: TaskProcessRecord, spec: dict, meta: dict) -> None:
    from pilot.internal.tasks.process import TaskProcess

    try:
        _, stderr = process.communicate(
            json.dumps({"callback": spec, "meta": meta}).encode(), timeout=CALLBACK_TIMEOUT_SECONDS
        )
    except subprocess.TimeoutExpired:
        _terminate_callback(process, record, TaskProcess(Path(meta["bench_root"])))
        raise RuntimeError(f"Callback timed out after {CALLBACK_TIMEOUT_SECONDS}s.") from None
    if process.returncode:
        raise RuntimeError(stderr.decode(errors="replace").strip() or "Callback failed.")


def _close_callback(process) -> None:
    if process.poll() is None:
        process.kill()
        process.wait(timeout=3)
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe:
            pipe.close()


def _terminate_callback(process, record, controller) -> None:
    from pilot.internal.tasks.process_identity import ProcessInspector

    inspector = ProcessInspector()
    deadline = time.monotonic() + 3
    parent = {os.getpid()}
    while inspector.owned_pids(record.identity) - parent and time.monotonic() < deadline:
        controller._signal(record, signal.SIGKILL, exclude_pids=parent)
        time.sleep(0.05)
    process.wait(timeout=3)


def _clear_stopped_callback(store, task_id, inspector, identity, argv) -> None:
    from pilot.internal.tasks.process_identity import ProcessOwnership

    if inspector.inspect(identity, argv) not in {ProcessOwnership.DEAD, ProcessOwnership.STALE}:
        return
    if not inspector.owned_pids(identity):
        store.remove_private_files(task_id, "process.json")


def run_stored_callback(task_dir: Path, trigger: str) -> None:
    callbacks_path = task_dir / "callbacks.json"
    if not callbacks_path.exists():
        return
    callbacks = json.loads(callbacks_path.read_text())
    callback = callbacks.get(trigger)
    if callback:
        run_bounded_callback(callback, json.loads((task_dir / "meta.json").read_text()), recovery=True)


if __name__ == "__main__":
    payload = json.load(sys.stdin)
    run_callback(payload["callback"], payload["meta"])
