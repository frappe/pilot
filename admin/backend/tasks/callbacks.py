import json
import os
import shutil
import subprocess
from pathlib import Path


def new_site_failure_callback(meta: dict) -> None:
    """Roll a failed/cancelled site create back to zero: drop the database (and
    bench.toml entry) then delete the site dir and strip its /etc/hosts line."""
    site_name = meta["args"]["name"]
    bench_root = Path(meta["bench_root"])
    _drop_site_best_effort(bench_root, site_name)
    shutil.rmtree(bench_root / "sites" / site_name, ignore_errors=True)
    _remove_from_hosts(site_name)


def _drop_site_best_effort(bench_root: Path, site_name: str) -> None:
    """Drop the DB + bench.toml entry via DropSiteCommand (frappe drop-site --force).
    Best effort: a half-created site or unreadable config must not raise."""
    try:
        from pilot.commands.drop_site import DropSiteCommand

        DropSiteCommand(_load_bench(bench_root), site_name).run()
    except Exception:
        pass


def _remove_from_hosts(site_name: str) -> None:
    hosts_path = "/etc/hosts"
    entry = f"127.0.0.1 {site_name}"
    try:
        lines = open(hosts_path).read().splitlines()
    except OSError:
        return

    kept = [line for line in lines if entry not in line.split("#", 1)[0].split()]
    if len(kept) == len(lines):
        return

    subprocess.run(
        ["sudo", "tee", hosts_path],
        input=("\n".join(kept) + "\n").encode(),
        capture_output=True,
        check=False,
    )


def ssl_setup_failure_callback(meta: dict) -> None:
    site_name = meta["args"]["site"]
    config_path = os.path.join(meta["bench_root"], "sites", site_name, "site_config.json")
    config = json.loads(open(config_path).read())
    config["ssl"] = False
    open(config_path, "w").write(json.dumps(config, indent=1))


def _created_apps(bench_root: Path, pre_existing: set[str]) -> list[str]:
    """App dirs present now but absent when the task started — never a pre-existing app."""
    apps_dir = bench_root / "apps"
    if not apps_dir.exists():
        return []
    return [entry.name for entry in apps_dir.iterdir() if entry.is_dir() and entry.name not in pre_existing]


def app_fetch_failure_callback(meta: dict) -> None:
    """Roll a failed/cancelled app fetch back to zero. Removes only apps this task created."""
    if "pre_existing_apps" not in meta:
        return  # no snapshot → can't prove safety
    bench_root = Path(meta["bench_root"])
    created = _created_apps(bench_root, set(meta["pre_existing_apps"]))
    if not created:
        return

    try:
        bench = _load_bench(bench_root)
    except Exception:
        bench = None  # broken config: skip site-uninstall/pip, still force-delete below
    for app_name in created:
        _teardown_app(bench, bench_root, app_name)


def _load_bench(bench_root: Path):
    from pilot.config.toml_store import BenchTomlStore
    from pilot.core.bench import Bench

    return Bench(BenchTomlStore.for_bench(bench_root).read(), bench_root)


def _teardown_app(bench, bench_root: Path, app_name: str) -> None:
    """Full removal via RemoveAppCommand; force-delete if it can't run on a half-built app."""
    if bench is None:
        _force_teardown(bench, bench_root, app_name)
        return
    try:
        from pilot.commands.remove_app import RemoveAppCommand

        RemoveAppCommand(bench, app_name, skip_confirm=True, force=True).run()
    except Exception:
        _force_teardown(bench, bench_root, app_name)


def _force_teardown(bench, bench_root: Path, app_name: str) -> None:
    apps_txt = bench_root / "sites" / "apps.txt"
    if apps_txt.exists():
        kept = [line for line in apps_txt.read_text().splitlines() if line.strip() != app_name]
        apps_txt.write_text("\n".join(kept) + ("\n" if kept else ""))
    try:
        from pilot.managers.python_env_manager import PythonEnvManager

        PythonEnvManager(bench).uninstall_app(app_name)
    except Exception:
        pass
    shutil.rmtree(bench_root / "apps" / app_name, ignore_errors=True)
