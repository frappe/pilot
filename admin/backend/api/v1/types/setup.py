from __future__ import annotations

from typing import Literal, TypedDict

from pilot.config import WorkerGroup

DatabaseEngine = Literal["mariadb", "postgres"]


DatabaseValidationState = Literal["valid", "invalid", "will_install"]


class SetupConfiguration(TypedDict):
    bench_name: str
    is_linux: bool
    native_process_manager: str
    python: str
    socketio_backend: str
    watch_apps_js: bool
    reload_python: bool
    watch_admin_js: bool
    db_type: str
    allow_developer_mode: bool
    mariadb_admin_user: str
    mariadb_socket_path: str
    mariadb_host: str
    mariadb_existing: bool
    mariadb_port: int
    postgres_admin_user: str
    postgres_port: int
    postgres_host: str
    postgres_existing: bool
    admin_enabled: bool
    admin_domain: str
    admin_tls: bool
    admin_jwks_url: str
    admin_jwks_audience: str
    admin_allow_bench_management: bool
    letsencrypt_email: str
    production_process_manager: str
    lite_mode_enabled: bool
    app_repo: str
    app_branch: str
    workers: list[WorkerGroup]
    admin_password_configured: bool
    mariadb_password_configured: bool
    postgres_password_configured: bool
    mariadb_local_available: bool
    postgres_local_available: bool
    running_setup_task_id: str | None


class FrameworkBranches(TypedDict):
    branches: list[str]


class DatabaseValidation(TypedDict):
    engine: DatabaseEngine
    state: DatabaseValidationState
