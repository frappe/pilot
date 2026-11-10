"""Admin API request benchmarks, from app creation through route handlers."""

from __future__ import annotations

from benchmark.fixtures import Fixture
from benchmark.harness import Case


def _setup_client(fixture: Fixture) -> None:
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session

    fixture.reset()
    app = create_app(fixture.bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    token, _ = Session(fixture.bench()).issue_session_token()
    client.set_cookie("sid", token)
    fixture.admin_app = app
    fixture.admin_client = client


def _setup_app(fixture: Fixture) -> None:
    from admin.backend.app import create_app

    fixture.reset()
    fixture.admin_app = create_app(fixture.bench_root)
    fixture.admin_app.config["TESTING"] = True
    fixture.admin_client = fixture.admin_app.test_client()


CASES = [
    Case(
        name="admin.create_app",
        group="admin-api",
        description="create_app(): Flask app, auth guard, every API blueprint, frontend routes",
        fn=lambda fixture: _create_app(fixture),
        setup=lambda fixture: fixture.reset(),
        iterations=20,
        warmup=3,
        profile=True,
    ),
    Case(
        name="admin.health",
        group="admin-api",
        description="GET /api/v1/health (unauthenticated)",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/health"),
        setup=_setup_app,
        iterations=100,
        warmup=10,
    ),
    Case(
        name="admin.bootstrap",
        group="admin-api",
        description="GET /api/v1/bootstrap (unauthenticated): config, init state, task activity",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/bootstrap"),
        setup=_setup_app,
        iterations=50,
        warmup=5,
        profile=True,
    ),
    Case(
        name="admin.session",
        group="admin-api",
        description="GET /api/v1/auth/session (authenticated): JWT verify and active-token write",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/auth/session"),
        setup=_setup_client,
        iterations=50,
        warmup=5,
        profile=True,
    ),
    Case(
        name="admin.benches",
        group="admin-api",
        description="GET /api/v1/benches: describe every bench in the directory",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/benches"),
        setup=_setup_client,
        iterations=30,
        warmup=5,
    ),
    Case(
        name="admin.bench_detail",
        group="admin-api",
        description="GET /api/v1/benches/bench: one bench's config and state",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/benches/bench"),
        setup=_setup_client,
        iterations=50,
        warmup=5,
    ),
    Case(
        name="admin.apps",
        group="admin-api",
        description="GET /api/v1/apps: git-backed app inventory",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/apps"),
        setup=_setup_client,
        iterations=20,
        warmup=3,
    ),
    Case(
        name="admin.app_detail",
        group="admin-api",
        description="GET /api/v1/apps/frappe: one app's metadata",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/apps/frappe"),
        setup=_setup_client,
        iterations=30,
        warmup=5,
    ),
    Case(
        name="admin.sites",
        group="admin-api",
        description="GET /api/v1/sites: site inventory with active apps and setup state",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/sites"),
        setup=_setup_client,
        iterations=20,
        warmup=3,
        profile=True,
    ),
    Case(
        name="admin.site_detail",
        group="admin-api",
        description="GET /api/v1/sites/site1.localhost: site config, apps, and URL",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/sites/site1.localhost"),
        setup=_setup_client,
        iterations=30,
        warmup=5,
    ),
    Case(
        name="admin.site_apps",
        group="admin-api",
        description="GET /api/v1/sites/site1.localhost/apps: installed and installable apps",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/sites/site1.localhost/apps"),
        setup=_setup_client,
        iterations=30,
        warmup=5,
    ),
    Case(
        name="admin.tasks",
        group="admin-api",
        description="GET /api/v1/tasks: task list with queue positions",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/tasks"),
        setup=_setup_client,
        iterations=30,
        warmup=5,
    ),
    Case(
        name="admin.task_worker",
        group="admin-api",
        description="GET /api/v1/task-worker: worker state and activity",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/task-worker"),
        setup=_setup_client,
        iterations=50,
        warmup=5,
    ),
    Case(
        name="admin.task_create",
        group="admin-api",
        description="POST /api/v1/tasks: queue a task through the API",
        fn=lambda fixture: fixture.admin_client.post(
            "/api/v1/tasks", json={"command": "clear-cache", "site": "site1.localhost"}
        ),
        setup=_setup_client,
        iterations=10,
        warmup=2,
    ),
    Case(
        name="admin.metrics",
        group="admin-api",
        description="GET /api/v1/metrics: psutil system metrics and IO deltas",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/metrics"),
        setup=_setup_client,
        iterations=50,
        warmup=5,
    ),
    Case(
        name="admin.logs",
        group="admin-api",
        description="GET /api/v1/logs: log file inventory",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/logs"),
        setup=_setup_client,
        iterations=50,
        warmup=5,
    ),
    Case(
        name="admin.log_tail",
        group="admin-api",
        description="GET /api/v1/logs/web.log: tail and search a log file",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/logs/web.log?lines=200"),
        setup=_setup_client,
        iterations=30,
        warmup=5,
    ),
    Case(
        name="admin.notifications",
        group="admin-api",
        description="GET /api/v1/notifications: notification feed",
        fn=lambda fixture: fixture.admin_client.get("/api/v1/notifications"),
        setup=_setup_client,
        iterations=50,
        warmup=5,
    ),
]


def _create_app(fixture: Fixture):
    from admin.backend.app import create_app

    return create_app(fixture.bench_root)
