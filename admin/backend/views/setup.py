from __future__ import annotations

from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from admin.backend.tasks.manager.task_reader import TaskReader
from admin.backend.tasks.manager.task_runner import TaskRunner
from bench_cli.config.bench_toml_builder import FRAMEWORK_BRANCHES, BenchTomlBuilder, current_port_offset

setup_bp = Blueprint("setup", __name__)


def wizard_marker_path(bench_root: Path) -> Path:
    """Marker that the bench is going through first-time setup via the wizard.

    Written when the wizard kicks off its setup task and cleared when setup
    finishes (and as a safety-net by /api/status once the bench is fully set up).
    It keeps /api/status on the wizard while init runs — env/bin/python can appear
    partway through, making the bench look 'initialized' before the task is done —
    so a reload returns to the wizard rather than a half-built dashboard.
    """
    return bench_root / ".wizard-active"


@setup_bp.route("/config")
def get_config():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    return jsonify(_read_defaults(bench_root))


@setup_bp.route("/branches")
def get_branches():
    return jsonify({"branches": FRAMEWORK_BRANCHES})


@setup_bp.route("/save", methods=["POST"])
def save_config():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}

    error = _validate(data)
    if error:
        return jsonify({"ok": False, "error": error}), 400

    # A fresh install can only secure the pre-existing 'root' account
    # (secure_installation runs ALTER USER 'root'@'localhost' — an arbitrary name
    # is never created), so reject a custom root user for fresh installs. An
    # existing DB can legitimately use a custom superuser.
    admin_user = data.get("mariadb_admin_user") or "root"
    if admin_user != "root" and _will_install_fresh(bench_root, data):
        return jsonify({"ok": False, "error":
            "A fresh MariaDB install only has the 'root' superuser; set the MariaDB root user to 'root'."}), 400

    # Preserve any settings the wizard didn't send (e.g. python version, fields
    # not shown in the current step). Incoming data wins on conflicts.
    toml_path = bench_root / "bench.toml"
    existing: dict = {}
    if toml_path.exists():
        try:
            existing = BenchTomlBuilder.read_settings(toml_path)
        except Exception:
            pass

    settings = {**existing, **data, "admin_enabled": True}
    content = BenchTomlBuilder(_current_name(bench_root), settings, port_offset=current_port_offset(toml_path)).render()
    toml_path.write_text(content)
    return jsonify({"ok": True})


@setup_bp.route("/validate-mariadb", methods=["POST"])
def validate_mariadb():
    """Tell the wizard whether the entered credentials will work.

    Dedicated instance: not yet provisioned → bench init will create it → will_install.
    Shared instance: must validate against the running system MariaDB.
    """
    from bench_cli.managers.mariadb_manager import MariaDBManager

    data = request.get_json(silent=True) or {}
    password = data.get("mariadb_password", "")
    admin_user = data.get("mariadb_admin_user", "root")
    dedicated = data.get("dedicated_db", True)  # True = dedicated, False = shared

    bench_root = Path(current_app.config["BENCH_ROOT"])
    config = _mariadb_config(bench_root, password, admin_user, dedicated=dedicated)
    manager = MariaDBManager(config)

    # Fresh install → init will install + secure it (the wizard locks the root
    # user to 'root' in this case, since secure_installation can only ALTER the
    # pre-existing root account).
    if _is_fresh_install(manager, dedicated):
        return jsonify({"state": "will_install"})

    if manager.check_credentials(password):
        return jsonify({"state": "valid"})

    return jsonify({"state": "invalid"})


def _is_fresh_install(manager, dedicated: bool) -> bool:
    """True when init will install/provision + secure MariaDB itself (rather than
    connecting to an already-configured server)."""
    if not manager.is_installed():
        return True
    # Dedicated instance not yet provisioned — init will create + secure it.
    if dedicated and manager.is_dedicated and not manager.service_is_active():
        return True
    return False


def _will_install_fresh(bench_root: Path, data: dict) -> bool:
    """Fresh-install check for the /save payload (shared if no instance name)."""
    from bench_cli.managers.mariadb_manager import MariaDBManager

    dedicated = bool(data.get("mariadb_instance"))
    config = _mariadb_config(
        bench_root,
        data.get("mariadb_password", ""),
        data.get("mariadb_admin_user") or "root",
        dedicated=dedicated,
    )
    return _is_fresh_install(MariaDBManager(config), dedicated)


def _mariadb_config(bench_root: Path, password: str, admin_user: str = "root", dedicated: bool = True):
    """Build a MariaDBConfig from the bench's toml with the entered credentials applied.

    For shared DB (dedicated=False) we don't read the bench toml — the toml may
    already have a dedicated instance name set (written by `bench new`), which would
    make the manager try the dedicated socket that doesn't exist yet.
    """
    from bench_cli.config.mariadb_config import MariaDBConfig

    config = MariaDBConfig(root_password=password, admin_user=admin_user)
    if dedicated:
        toml_path = bench_root / "bench.toml"
        if toml_path.exists():
            try:
                settings = BenchTomlBuilder.read_settings(toml_path)
                config.instance = settings.get("mariadb_instance", "") or ""
                config.socket_path = settings.get("mariadb_socket_path", "") or ""
            except Exception:
                pass
    return config


def _validate(data: dict) -> str | None:
    for field in ("mariadb_password", "admin_password"):
        if not data.get(field):
            return f"{field} is required"
    if data.get("volume_enabled", True):
        if not data.get("volume_pool"):
            return "volume_pool is required"
        backing = data.get("volume_backing", "auto")
        if backing == "device" and not data.get("volume_device"):
            return "volume_device is required when volume backing is a block device"
        if backing == "image" and not data.get("volume_image_size"):
            return "volume_image_size is required when volume backing is a disk image"
    return None


@setup_bp.route("/start", methods=["POST"])
def start_setup():
    """Run the wizard as one task that initializes the bench — see WizardSetupTask.

    A single task means the wizard follows one continuous output stream and, on a
    reload, simply reattaches to the one running task. Production is a separate
    step the user runs from the terminal afterwards (`bench setup production`).
    """
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.managers.volume_manager import VolumeManager
    from bench_cli.platform import has_passwordless_sudo, is_linux

    bench_root = Path(current_app.config["BENCH_ROOT"])

    # The wizard runs as a no-TTY task; without passwordless sudo it would hang on
    # a hidden password prompt. Surface a clean error instead.
    if is_linux() and not has_passwordless_sudo():
        return jsonify({"ok": False, "error": "Passwordless sudo is not configured. "
                        "Run install.sh (or add /etc/sudoers.d/<user> NOPASSWD) and retry."}), 400

    # Pre-flight validation so config/volume errors surface in the wizard instead
    # of failing deep inside the task.
    try:
        config = BenchConfig.from_file(bench_root / "bench.toml")
        config.validate()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    if config.volume.enabled:
        if error := VolumeManager(config.volume).validate_sizes_fit_backing():
            return jsonify({"ok": False, "error": error}), 400

    try:
        # Reattach to an in-flight run rather than starting a second one (e.g. the
        # page reloaded and re-posted on resume).
        existing = _running_setup_task(bench_root)
        if existing:
            return jsonify({"ok": True, "task_id": existing.task_id})
        task_id = TaskRunner(bench_root).run("wizard-setup", {})
        # Mark the wizard as owning this bench until setup finishes, so a reload
        # mid-run returns to the wizard rather than the half-built dashboard.
        wizard_marker_path(bench_root).touch()
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@setup_bp.route("/finish", methods=["POST"])
def finish_setup():
    """Shut down the standalone wizard server so the user can run `bench start`.

    Only the wizard server (started with --wizard) may be shut down this way —
    the procfile-managed admin process must never exit, since the dev-mode
    runner stops the whole bench when any one process dies.
    """
    import os
    import signal
    import threading

    # Setup is over: drop the wizard marker regardless of how this admin is run,
    # so the dashboard takes over (the procfile admin returns 400 below but must
    # still clear the marker).
    bench_root = Path(current_app.config["BENCH_ROOT"])
    wizard_marker_path(bench_root).unlink(missing_ok=True)

    if not current_app.config.get("WIZARD_SERVER"):
        return jsonify({"ok": False, "error": "Not running as the setup-wizard server"}), 400

    if not (bench_root / "config" / "Procfile").exists():
        return jsonify({"ok": False, "error": "Bench is not initialized yet"}), 400

    # call_on_close fires after the response body has been written to the
    # socket, so the kill can't race ahead of the response. The tiny timer
    # just lets the handler thread finish tearing down the connection.
    response = jsonify({"ok": True})
    response.call_on_close(lambda: threading.Timer(0.1, lambda: os.kill(os.getpid(), signal.SIGTERM)).start())
    return response


@setup_bp.route("/new-site", methods=["POST"])
def start_new_site():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"ok": False, "error": "Site name is required"}), 400

    args = {"name": data["name"]}
    if data.get("admin_password"):
        args["admin_password"] = data["admin_password"]
    try:
        task_id = TaskRunner(bench_root).run("new-site", args)
        return jsonify({"ok": True, "task_id": task_id})
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500


@setup_bp.route("/stream/<task_id>")
def stream_task(task_id: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    reader = TaskReader(bench_root)

    def generate():
        for line in reader.stream_output(task_id):
            if line.startswith("__DONE__:"):
                yield f"event: done\ndata: {line[9:]}\n\n"
            elif line.startswith("__CR__:"):
                yield f"event: overwrite\ndata: {line[7:]}\n\n"
            else:
                yield f"data: {line}\n\n"

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


def _read_defaults(bench_root: Path) -> dict:
    from bench_cli.platform import is_linux, native_process_manager

    result = {
        "bench_name": bench_root.name,
        "is_linux": is_linux(),
        "native_process_manager": native_process_manager(),
        **BenchTomlBuilder.DEFAULTS,
    }
    toml_path = bench_root / "bench.toml"
    if toml_path.exists():
        try:
            result.update(BenchTomlBuilder.read_settings(toml_path))
            if not result.get("bench_name"):
                result["bench_name"] = bench_root.name
        except Exception:
            pass

    result.update(_volume_suggestions(toml_path))

    try:
        task = _running_setup_task(bench_root)
        result["running_setup_task_id"] = task.task_id if task else None
    except Exception:
        result["running_setup_task_id"] = None

    return result


def _running_setup_task(bench_root: Path):
    """The wizard's setup task if it's currently running, else None. The single
    live task is the whole resume signal: a reload reattaches to it."""
    return next(
        (t for t in TaskReader(bench_root).list_tasks()
         if t.command == "wizard-setup" and t.status == "running"),
        None,
    )


def _volume_suggestions(toml_path: Path) -> dict:
    """Smart volume defaults for the wizard.

    Fresh setups (no [volume] table yet) get discovery-driven defaults:
    device backing on the largest unused disk, or image backing sized from
    rootfs free space, with quotas/reservations derived from the backing size.
    Existing volume config is never overridden — only the discovered device
    list is returned so the UI can still offer a dropdown.
    """
    from bench_cli.platform import is_linux

    if not is_linux():
        return {"available_devices": []}

    from bench_cli.managers.volume_manager import (
        compute_smart_defaults,
        list_device_choices,
        rootfs_free_bytes,
    )

    slider_bounds = {"rootfs_free_bytes": rootfs_free_bytes()}

    try:
        import tomllib

        with open(toml_path, "rb") as f:
            has_volume_config = "volume" in tomllib.load(f)
    except Exception:
        has_volume_config = False

    if has_volume_config:
        return {"available_devices": list_device_choices(), **slider_bounds}
    return {**compute_smart_defaults(), **slider_bounds}


def _current_name(bench_root: Path) -> str:
    toml_path = bench_root / "bench.toml"
    if not toml_path.exists():
        return bench_root.name
    try:
        return BenchTomlBuilder.read_settings(toml_path).get("bench_name") or bench_root.name
    except Exception:
        return bench_root.name
