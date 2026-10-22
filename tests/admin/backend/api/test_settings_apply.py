from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

from admin.backend.api.v1.settings import settings_bp
from admin.backend.api.v1.settings.config import _GROUP_TYPES
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


@pytest.mark.parametrize("group", sorted(_GROUP_TYPES))
@pytest.mark.parametrize("value", [1, "text", True])
def test_settings_reject_a_malformed_group(tmp_path: Path, group: str, value) -> None:
    bench_root = tmp_path / "bench"
    client = _client(bench_root)
    before = (bench_root / "bench.toml").read_text()

    response = client.patch("/api/v1/settings", json={group: value})

    assert response.status_code == 422
    assert response.get_json()["error"]["code"] == "invalid_settings"
    assert (bench_root / "bench.toml").read_text() == before


def test_settings_reject_an_object_where_workers_expects_a_list(tmp_path: Path) -> None:
    client = _client(tmp_path / "bench")

    response = client.patch("/api/v1/settings", json={"workers": {"queues": ["default"]}})

    assert response.status_code == 422
    assert response.get_json()["error"]["message"] == "workers must be a list."


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
    # of band (`pilot setup production`), not via the settings patcher.
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


def test_a_port_change_on_production_rewrites_nginx_after_the_restart(tmp_path: Path) -> None:
    def enable_production(config: BenchConfig) -> None:
        config.production.enabled = True
        config.production.process_manager = "systemd"

    client = _client(tmp_path / "bench", enable_production)
    calls: list[str] = []
    with (
        patch("pilot.core.bench.settings.regenerate_configs", side_effect=lambda bench: calls.append("units")),
        patch(
            "pilot.core.bench.settings.restart_running_workload",
            side_effect=lambda bench: calls.append("restart") or True,
        ),
        patch("pilot.core.bench.settings.regenerate_nginx", side_effect=lambda bench: calls.append("nginx")),
    ):
        response = client.patch("/api/v1/settings", json={"bench": {"http_port": 8123}})

    assert response.status_code == 200
    assert calls == ["units", "restart", "nginx"]


def test_a_failed_restart_on_production_still_rewrites_nginx(tmp_path: Path) -> None:
    def enable_production(config: BenchConfig) -> None:
        config.production.enabled = True
        config.production.process_manager = "systemd"

    client = _client(tmp_path / "bench", enable_production)
    with (
        patch("pilot.core.bench.settings.regenerate_configs"),
        patch("pilot.core.bench.settings.restart_running_workload", side_effect=TimeoutError()),
        patch("pilot.core.bench.settings.regenerate_nginx") as regenerate_nginx,
    ):
        response = client.patch("/api/v1/settings", json={"bench": {"http_port": 8123}})

    assert response.get_json()["error"]["code"] == "service_restart_failed"
    regenerate_nginx.assert_called_once()
