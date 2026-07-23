from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from flask import Flask

from admin.backend.api.v1.settings import settings_bp
from pilot.config import BenchConfig


def _client(bench_root: Path, configure=None):
    bench_root.mkdir()
    config = BenchConfig.from_flat(
        bench_root.name,
        {
            "admin_domain": "admin.example.com",
            "admin_password": "secret",
        },
    )
    if configure:
        configure(config)
    (bench_root / "bench.toml").write_text(config.dumps())
    app = Flask(__name__)
    app.config["BENCH_ROOT"] = bench_root
    app.register_blueprint(settings_bp, url_prefix="/api/v1/settings")
    return app.test_client()


def _worker_update() -> dict:
    return {"workers": [{"queues": ["default"], "count": 2}]}


def test_settings_report_config_generation_failure_after_save(tmp_path: Path) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    with patch(
        "pilot.core.bench.settings.regenerate_configs",
        side_effect=RuntimeError("secret generator detail"),
    ):
        response = client.patch("/api/v1/settings", json=_worker_update())

    assert response.status_code == 500
    assert response.get_json() == {
        "error": {
            "code": "configuration_generation_failed",
            "details": {"saved": True},
            "message": "Settings were saved, but service configuration could not be regenerated.",
        }
    }
    assert b"secret generator detail" not in response.data
    assert BenchConfig.from_file(bench_root / "bench.toml").workers.groups[0].count == 2


def test_settings_report_restart_failure_without_stderr(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")
    with (
        patch("pilot.core.bench.settings.regenerate_configs"),
        patch(
            "pilot.core.bench.settings.restart_running_workload",
            side_effect=RuntimeError("secret supervisor stderr"),
        ),
    ):
        response = client.patch("/api/v1/settings", json=_worker_update())

    assert response.status_code == 500
    assert response.get_json()["error"] == {
        "code": "service_restart_failed",
        "details": {"saved": True},
        "message": "Settings were saved, but running services could not be restarted.",
    }
    assert b"secret supervisor stderr" not in response.data


def test_settings_report_nginx_failure_without_exception_text(tmp_path: Path) -> None:
    def enable_production(config: BenchConfig) -> None:
        config.production.enabled = True
        config.production.process_manager = "systemd"

    # nginx is only regenerated on a production bench; production is set up out
    # of band (`bench setup production`), not via the settings patcher.
    client = _client(tmp_path / "bench", enable_production)
    update = {
        "firewall": {
            "enabled": True,
            "default": "allow",
            "rules": [],
        },
    }
    with (
        patch("pilot.core.bench.settings.regenerate_configs"),
        patch("pilot.core.bench.settings.restart_running_workload", return_value=False),
        patch(
            "pilot.core.bench.settings.regenerate_nginx",
            side_effect=RuntimeError("secret nginx detail"),
        ),
    ):
        response = client.patch("/api/v1/settings", json=update)

    assert response.status_code == 500
    assert response.get_json()["error"] == {
        "code": "nginx_apply_failed",
        "details": {"saved": True},
        "message": "Settings were saved, but nginx could not apply them.",
    }
    assert b"secret nginx detail" not in response.data


def test_settings_success_has_no_legacy_error_fields(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")
    with (
        patch("pilot.core.bench.settings.regenerate_configs"),
        patch("pilot.core.bench.settings.restart_running_workload", return_value=True),
    ):
        response = client.patch("/api/v1/settings", json=_worker_update())

    assert response.status_code == 200
    assert response.get_json() == {"restarted": True}
