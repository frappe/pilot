import json
import logging
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from pilot.tasks.manager.task_state import TaskStatus
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.utils import hosts_line_contains

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
    site_name = args["site"]
    bench_root = Path(meta["bench_root"])
    site_path = _safe_site_path(bench_root, site_name)
    if not _drop_failed_site(bench_root, site_name, site_path):
        raise RuntimeError(
            f"Could not clean up partial site {site_name!r}; "
            "its files were preserved for recovery."
        )
    shutil.rmtree(site_path, ignore_errors=True)
    _remove_from_hosts(site_name)


def _drop_failed_site(bench_root: Path, site_name: str, site_path: Path) -> bool:
    if not (site_path / "site_config.json").is_file():
        return True
    try:
        from pilot.commands.sites.delete import DropSiteCommand
        from pilot.config.toml_store import BenchTomlStore
        from pilot.core.bench import Bench
        from pilot.managers.platform import noninteractive_privileges

        config = BenchTomlStore.for_bench(bench_root).read()
        bench = Bench(config, bench_root)
        with noninteractive_privileges():
            DropSiteCommand(bench, site_name).run()
        return True
    except Exception as exc:
        logging.debug("Site drop callback failed for %s: %s", site_name, exc)
        return False


def _remove_from_hosts(site_name: str) -> None:
    try:
        lines = _HOSTS_PATH.read_text().splitlines()
    except OSError:
        return

    kept = [line for line in lines if not hosts_line_contains(line, site_name)]
    if len(kept) == len(lines):
        return

    subprocess.run(
        ["sudo", "-n", "tee", str(_HOSTS_PATH)],
        input=("\n".join(kept) + "\n").encode(),
        capture_output=True,
        check=False,
    )


def _disable_site_ssl(meta: dict, args: dict) -> None:
    site_name = args["site"]
    config_path = (
        _safe_site_path(Path(meta["bench_root"]), site_name) / "site_config.json"
    )
    with exclusive_file_lock(config_path):
        config = json.loads(config_path.read_text())
        config["ssl"] = False
        replace_private_text_locked(config_path, json.dumps(config, indent=1))


def _cleanup_site_restore(meta: dict, args: dict) -> None:
    bench_root = Path(meta["bench_root"]).resolve()
    expected_upload_root = bench_root / "tmp" / "uploads"
    upload_root = expected_upload_root.resolve()
    if upload_root != expected_upload_root:
        raise ValueError("Restore upload root must stay within the bench.")
    upload_dir = Path(args["upload_dir"])
    resolved_upload_dir = upload_dir.resolve(strict=False)
    if resolved_upload_dir.parent != upload_root or upload_dir.is_symlink():
        raise ValueError("Restore upload path must stay within the bench.")

    shutil.rmtree(upload_dir, ignore_errors=True)
    if args.get("remove_site"):
        _remove_failed_site(meta, {"site": args["site"]})


_OPERATIONS: dict[str, CallbackOperation] = {
    "cleanup-site-restore": _cleanup_site_restore,
    "remove-failed-site": _remove_failed_site,
    "disable-site-ssl": _disable_site_ssl,
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


def run_stored_callback(task_dir: Path, trigger: str) -> None:
    callbacks_path = task_dir / "callbacks.json"
    if not callbacks_path.exists():
        return
    callbacks = json.loads(callbacks_path.read_text())
    callback = callbacks.get(trigger)
    if callback:
        run_callback(callback, json.loads((task_dir / "meta.json").read_text()))
