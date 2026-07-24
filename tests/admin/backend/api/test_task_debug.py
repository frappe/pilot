"""Tests for the failed-task AI debug SSE endpoint."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from flask import Flask

from admin.backend.api.v1.tasks import tasks_bp
from pilot.config import BenchConfig
from pilot.internal.tasks.store import TaskStore
from pilot.managers.task.models import TaskStatus

TASK_ID = "20260715-120000-aabbcc"


def _client(bench_root: Path):
    app = Flask(__name__)
    app.config["BENCH_ROOT"] = bench_root
    app.register_blueprint(tasks_bp, url_prefix="/api/v1/tasks")
    return app.test_client()


def _write_bench(bench_root: Path, *, connect_ai: bool = True) -> None:
    bench_root.mkdir(exist_ok=True)
    config = BenchConfig.default("test-bench")
    if connect_ai:
        config.llm.provider = "openai"
        config.llm.api_key = "sk-key"
        config.llm.model = "gpt-4o"
    config.write(bench_root)


def _make_task(bench_root: Path, status: TaskStatus) -> None:
    store = TaskStore(bench_root)
    store.create_queued(
        {
            "task_id": TASK_ID,
            "command": "migrate",
            "args": {"site": "demo.local"},
            "command_argv": ["bench", "migrate"],
            "queued_at": "2026-07-15T12:00:00+00:00",
            "started_at": None,
            "finished_at": None,
            "exit_code": None,
            "failure": None,
            "bench_root": str(bench_root),
        }
    )
    store.transition(TASK_ID, TaskStatus.QUEUED, TaskStatus.RUNNING)
    store.transition(
        TASK_ID,
        TaskStatus.RUNNING,
        status,
        {"finished_at": "2026-07-15T12:00:02+00:00", "exit_code": 1},
    )


class _FakeIntegration:
    def prompt(self, *args, **kwargs):
        self.received = (args, kwargs)
        return "stream-handle"

    def iter_response_text(self, stream):
        yield "Root "
        yield "cause: bad migration."


def test_debug_streams_ai_explanation(tmp_path: Path) -> None:
    _write_bench(tmp_path, connect_ai=True)
    _make_task(tmp_path, TaskStatus.FAILED)

    with patch("pilot.managers.task.debug.build_integration", return_value=_FakeIntegration()):
        response = _client(tmp_path).get(f"/api/v1/tasks/{TASK_ID}/debug")

    assert response.status_code == 200
    assert response.mimetype == "text/event-stream"
    body = response.get_data(as_text=True)
    assert "Root " in body
    assert "cause: bad migration." in body
    assert '"type":"done"' in body


def test_debug_rejects_non_failed_task(tmp_path: Path) -> None:
    _write_bench(tmp_path, connect_ai=True)
    _make_task(tmp_path, TaskStatus.SUCCESS)

    response = _client(tmp_path).get(f"/api/v1/tasks/{TASK_ID}/debug")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "task_not_failed"


def test_debug_requires_connected_ai(tmp_path: Path) -> None:
    _write_bench(tmp_path, connect_ai=False)
    _make_task(tmp_path, TaskStatus.FAILED)

    response = _client(tmp_path).get(f"/api/v1/tasks/{TASK_ID}/debug")
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "ai_not_configured"


def test_debug_missing_task_is_404(tmp_path: Path) -> None:
    _write_bench(tmp_path, connect_ai=True)
    response = _client(tmp_path).get("/api/v1/tasks/nope/debug")
    assert response.status_code == 404
