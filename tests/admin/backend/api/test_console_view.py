from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.config import BenchConfig
from pilot.core.site.console import python_completer


def _client(tmp_path: Path):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    bench_root = tmp_path / "benches" / "current"
    (bench_root / "sites").mkdir(parents=True)
    config = BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": "secret"})
    (bench_root / "bench.toml").write_text(config.dumps())
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def test_completions_report_low_memory(tmp_path: Path) -> None:
    client = _client(tmp_path)
    with (
        patch.object(python_completer, "can_read_memory", return_value=True),
        patch.object(python_completer, "available_memory_mb", return_value=100),
    ):
        response = client.post(
            "/api/v1/console/completions", json={"code": "frappe.", "line": 1, "column": 7}
        )
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "low_memory"
