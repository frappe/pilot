import json
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path

from pilot.secure_files import write_private_text

CallbackOperation = Callable[[dict, dict], None]


def _remove_failed_site(meta: dict, args: dict) -> None:
    site_name = args["site"]
    site_path = Path(meta["bench_root"]) / "sites" / site_name
    shutil.rmtree(site_path, ignore_errors=True)
    _remove_from_hosts(site_name)


def _remove_from_hosts(site_name: str) -> None:
    hosts_path = Path("/etc/hosts")
    entry = f"127.0.0.1 {site_name}"
    try:
        lines = hosts_path.read_text().splitlines()
    except OSError:
        return

    kept = [line for line in lines if entry not in line.split("#", 1)[0].split()]
    if len(kept) == len(lines):
        return

    subprocess.run(
        ["sudo", "tee", str(hosts_path)],
        input=("\n".join(kept) + "\n").encode(),
        capture_output=True,
        check=False,
    )


def _disable_site_ssl(meta: dict, args: dict) -> None:
    site_name = args["site"]
    config_path = Path(meta["bench_root"]) / "sites" / site_name / "site_config.json"
    config = json.loads(config_path.read_text())
    config["ssl"] = False
    write_private_text(config_path, json.dumps(config, indent=1))


_OPERATIONS: dict[str, CallbackOperation] = {
    "remove-failed-site": _remove_failed_site,
    "disable-site-ssl": _disable_site_ssl,
}


def validate_callback(spec: dict) -> dict:
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
