from __future__ import annotations

import typing
from pathlib import Path

from flask import Blueprint, Response, current_app, jsonify, request, stream_with_context

from admin.backend.tasks.manager.task_reader import TaskReader
from admin.backend.tasks.manager.events import sse_message
from admin.backend.tasks.manager.task_runner import TaskRunner
from admin.backend.tasks.manager.task_state import ACTIVE_TASK_STATUSES
from pilot.config.bench_toml_builder import (
    FRAMEWORK_BRANCHES,
    BenchTomlBuilder,
    current_port_offset,
)
from pilot.config.toml_store import BenchTomlStore

if typing.TYPE_CHECKING:
    from pilot.managers.mariadb_manager import MariaDBManager
    from pilot.managers.postgres_manager import PostgresManager

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

    # Preserve any settings the wizard didn't send (e.g. python version, fields
    # not shown in the current step). Incoming data wins on conflicts.
    toml_path = bench_root / "bench.toml"
    store = BenchTomlStore(toml_path)
    existing: dict = {}
    if toml_path.exists():
        try:
            existing = store.read_flat()
        except Exception:
            pass

    settings = {**existing, **data, "admin_enabled": True}
    store.write_flat(
        _current_name(bench_root), settings, port_offset=current_port_offset(toml_path)
    )

    resp = jsonify({"ok": True})
    # Setting the password closes the open setup phase; hand back a session so the
    # next request (e.g. /start) authenticates instead of 401ing.
    if settings.get("admin_password"):
        _issue_setup_session(resp, toml_path)
    return resp


def _issue_setup_session(resp, toml_path: Path) -> None:
    from pilot.commands.generate_session import ensure_jwt_secret, issue_token

    resp.set_cookie(
        "sid",
        issue_token(ensure_jwt_secret(toml_path)),
        max_age=24 * 3600,
        httponly=True,
        samesite="Lax",
    )


@setup_bp.route("/validate-mariadb", methods=["POST"])
def validate_mariadb():
    """Tell the wizard whether the entered credentials will work against the
    single MariaDB server every bench for this OS user shares. Not yet
    provisioned → bench init will create and secure it → will_install."""
    from pilot.managers.mariadb_manager import MariaDBManager

    data = request.get_json(silent=True) or {}
    password = data.get("mariadb_password", "")
    admin_user = data.get("mariadb_admin_user", "root")
    host = data.get("mariadb_host", "")
    port = data.get("mariadb_port")
    existing = bool(data.get("mariadb_existing"))

    bench_root = Path(current_app.config["BENCH_ROOT"])
    config = _mariadb_config(bench_root, password, admin_user, host, port, existing)
    manager = MariaDBManager(config)

    # existing is a deliberate choice, never inferred from host: just check the login.
    if config.existing:
        return jsonify({"state": "valid" if manager.check_credentials(password) else "invalid"})

    if _is_fresh_install(manager):
        return jsonify({"state": "will_install"})

    if manager.check_credentials(password):
        return jsonify({"state": "valid"})

    return jsonify({"state": "invalid"})


@setup_bp.route("/validate-postgres", methods=["POST"])
def validate_postgres():
    """Tell the wizard whether the entered PostgreSQL credentials will work
    against the single PostgreSQL server every bench for this OS user shares."""
    from pilot.config.postgres_config import PostgresConfig
    from pilot.managers.postgres_manager import PostgresManager

    data = request.get_json(silent=True) or {}
    password = data.get("postgres_password", "")
    admin_user = data.get("postgres_admin_user") or "postgres"
    host = data.get("postgres_host") or "localhost"
    port = int(data.get("postgres_port") or 5432)
    existing = bool(data.get("postgres_existing"))

    config = PostgresConfig(
        host=host, port=port, root_password=password, admin_user=admin_user, existing=existing
    )
    manager = PostgresManager(config)

    if config.existing:
        return jsonify({"state": "valid" if manager.check_credentials(password) else "invalid"})

    if _is_fresh_install(manager):
        return jsonify({"state": "will_install"})
    if manager.check_credentials(password):
        return jsonify({"state": "valid"})
    return jsonify({"state": "invalid"})


def _is_fresh_install(manager: PostgresManager | MariaDBManager) -> bool:
    """True when init will install/provision + secure the server itself
    (rather than connecting to an already-configured one). is_provisioned()
    checks for the manager's own systemd --user unit — the single source of
    truth for whether this bench user's server has already been set up."""
    if not manager.is_installed():
        return True
    return not manager.is_provisioned()


def _mariadb_config(
    bench_root: Path,
    password: str,
    admin_user: str = "root",
    host: str = "",
    port=None,
    existing: bool = False,
):
    """Build a MariaDBConfig from the bench's toml with the entered credentials applied."""
    from pilot.config.mariadb_config import MariaDBConfig

    config = MariaDBConfig(
        root_password=password,
        admin_user=admin_user,
        host=host or "localhost",
        port=int(port or 3306),
        existing=existing,
    )
    toml_path = bench_root / "bench.toml"
    if toml_path.exists():
        try:
            settings = BenchTomlStore(toml_path).read_flat()
            config.socket_path = settings.get("mariadb_socket_path", "") or ""
        except Exception:
            pass
    return config


def _validate(data: dict) -> str | None:
    if not data.get("admin_password"):
        return "admin_password is required"
    # Each server-based engine needs its superuser password: frappe connects over
    # TCP, where a blank password fails password auth and would only surface at
    # first site creation. init sets this password on a fresh install.
    db_type = data.get("db_type", "mariadb")
    if db_type == "mariadb" and not data.get("mariadb_password"):
        return "mariadb_password is required"
    if db_type == "postgres" and not data.get("postgres_password"):
        return "postgres_password is required"
    if data.get(f"{db_type}_existing"):
        if not data.get(f"{db_type}_host"):
            return f"{db_type}_host is required when connecting to an existing database server"
        if not data.get(f"{db_type}_admin_user"):
            return (
                f"{db_type}_admin_user is required when connecting to an existing database server"
            )
    return None


@setup_bp.route("/start", methods=["POST"])
def start_setup():
    """Run the wizard as one task that initializes the bench — see WizardSetupTask.

    A single task means the wizard follows one continuous output stream and, on a
    reload, simply reattaches to the one running task. Production is a separate
    step the user runs from the terminal afterwards (`bench setup production`).
    """
    bench_root = Path(current_app.config["BENCH_ROOT"])

    # No passwordless-sudo preflight needed: bench init runs rootless (a
    # per-bench-user systemd --user MariaDB/PostgreSQL server — see
    # MariaDBManager/PostgresManager), except on Alpine, which commonly
    # already runs as root.

    # Pre-flight validation so config errors surface in the wizard instead of
    # failing deep inside the task.
    try:
        config = BenchTomlStore.for_bench(bench_root).read()
        config.validate()
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

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
    response.call_on_close(
        lambda: threading.Timer(0.1, lambda: os.kill(os.getpid(), signal.SIGTERM)).start()
    )
    return response


@setup_bp.route("/new-site", methods=["POST"])
def start_new_site():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True) or {}
    if not data.get("name"):
        return jsonify({"ok": False, "error": "Site name is required"}), 400
    admin_password = data.get("admin_password")
    if not isinstance(admin_password, str) or not admin_password.strip():
        import secrets

        admin_password = secrets.token_urlsafe(16)

    args = {"name": data["name"], "admin_password": admin_password}
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
        for event in reader.stream_output(task_id):
            yield sse_message(event)

    return Response(stream_with_context(generate()), mimetype="text/event-stream")


_PASSWORD_KEYS = ("mariadb_password", "postgres_password")


def _read_defaults(bench_root: Path) -> dict:
    from pilot.platform import is_linux, native_process_manager

    # This is a read endpoint the wizard polls before login — it must never echo
    # a DB password back, default or real, whether or not bench.toml has one set.
    defaults = {k: v for k, v in BenchTomlBuilder.DEFAULTS.items() if k not in _PASSWORD_KEYS}

    result = {
        "bench_name": bench_root.name,
        "is_linux": is_linux(),
        "native_process_manager": native_process_manager(),
        **defaults,
    }
    toml_path = bench_root / "bench.toml"
    if toml_path.exists():
        try:
            settings = BenchTomlStore(toml_path).read_flat()
            for key in _PASSWORD_KEYS:
                settings.pop(key, None)
            result.update(settings)
            if not result.get("bench_name"):
                result["bench_name"] = bench_root.name
        except Exception:
            pass

    result.pop("admin_password", None)

    try:
        task = _running_setup_task(bench_root)
        result["running_setup_task_id"] = task.task_id if task else None
    except Exception:
        result["running_setup_task_id"] = None

    return result


def _running_setup_task(bench_root: Path):
    """The wizard's setup task if it's queued or running, else None. The single
    live task is the whole resume signal: a reload reattaches to it."""
    return next(
        (
            t
            for t in TaskReader(bench_root).list_tasks()
            if t.command == "wizard-setup" and t.status in ACTIVE_TASK_STATUSES
        ),
        None,
    )


def _current_name(bench_root: Path) -> str:
    toml_path = bench_root / "bench.toml"
    if not toml_path.exists():
        return bench_root.name
    try:
        return BenchTomlStore(toml_path).read_flat().get("bench_name") or bench_root.name
    except Exception:
        return bench_root.name
