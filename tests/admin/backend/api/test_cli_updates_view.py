"""Tests for GET /api/v1/cli-updates and POST /api/v1/cli-update-checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from pilot.config import BenchConfig
from pilot.config.common import CommonConfig
from pilot.exceptions import CronError


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": password}).dumps()
    )
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def _mock_repo(behind: int) -> Mock:
    repo = Mock()
    repo.branch = "main"
    repo.count.return_value = behind
    repo.commit_subject.return_value = "a commit"
    repo.last_fetched = None
    return repo


def test_cli_updates_reads_without_fetching(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    repo = _mock_repo(behind=2)

    with (
        patch("admin.backend.api.v1.updates.cli_root", return_value=Path("/cli")),
        patch("admin.backend.api.v1.updates.GitRepo", return_value=repo),
    ):
        response = client.get("/api/v1/cli-updates")

    body = response.get_json()
    assert response.status_code == 200
    assert body["commits_behind"] == 2
    assert body["update_available"] is True
    repo.fetch.assert_not_called()


def test_cli_update_checks_fetches_first(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    repo = _mock_repo(behind=0)

    with (
        patch("admin.backend.api.v1.updates.cli_root", return_value=Path("/cli")),
        patch("admin.backend.api.v1.updates.GitRepo", return_value=repo),
    ):
        response = client.post("/api/v1/cli-update-checks")

    body = response.get_json()
    assert response.status_code == 200
    assert body["update_available"] is False
    repo.fetch.assert_called_once_with("main", timeout=60)


def test_cli_updates_release_reports_version_without_network(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with (
        patch("pilot.is_dev_build", False),
        patch("pilot.__version__", "v0.0.1-pre-alpha"),
    ):
        response = client.get("/api/v1/cli-updates")

    body = response.get_json()
    assert response.status_code == 200
    assert body["is_dev"] is False
    assert body["current_version"] == "v0.0.1-pre-alpha"
    assert body["update_available"] is False
    assert body["restarts_admin"] is False  # no systemd or supervisor runs this bench


def test_cli_update_checks_release_compares_latest(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with (
        patch("pilot.is_dev_build", False),
        patch("pilot.__version__", "v0.0.1-pre-alpha"),
        patch("pilot.updater.update_available", return_value=(True, "v0.0.2-pre-alpha")),
    ):
        response = client.post("/api/v1/cli-update-checks")

    body = response.get_json()
    assert response.status_code == 200
    assert body["update_available"] is True
    assert body["latest_version"] == "v0.0.2-pre-alpha"


@pytest.fixture
def auto_update_client(tmp_path, monkeypatch):
    monkeypatch.setattr("pilot.utils.benches_dir", lambda: tmp_path)
    monkeypatch.setattr("pilot.is_dev_build", False)
    return _client(tmp_path / "current")


def test_automatic_updates_save_for_the_host_and_manage_cron(auto_update_client, tmp_path):
    with patch("pilot.core.server.auto_update.CronManager") as cron:
        response = auto_update_client.patch("/api/v1/cli-auto-update", json={"enabled": True})
        assert response.status_code == 200
        assert response.get_json() == {"auto_update": True}
        assert CommonConfig.read(tmp_path).updates.auto_pilot_update
        assert cron.return_value.set_schedule.call_args.args[1] == "* * * * *"
        assert auto_update_client.get("/api/v1/cli-updates").get_json()["auto_update"] is True

        response = auto_update_client.patch("/api/v1/cli-auto-update", json={"enabled": False})
        assert response.status_code == 200
        assert response.get_json() == {"auto_update": False}
        assert not CommonConfig.read(tmp_path).updates.auto_pilot_update
        cron.return_value.remove_schedule.assert_called_once_with("pilot-auto-update")


@pytest.mark.parametrize("payload", [None, [], {}, {"enabled": "false"}, {"enabled": 1}])
def test_automatic_updates_require_a_boolean(auto_update_client, payload, tmp_path):
    response = auto_update_client.patch("/api/v1/cli-auto-update", json=payload)
    assert response.status_code == 400
    assert not CommonConfig.path(tmp_path).exists()


def test_automatic_updates_are_unavailable_for_source_installs(auto_update_client, monkeypatch):
    monkeypatch.setattr("pilot.is_dev_build", True)
    response = auto_update_client.patch("/api/v1/cli-auto-update", json={"enabled": True})
    assert response.status_code == 409
    assert response.get_json()["error"]["code"] == "auto_update_unavailable"


@pytest.mark.parametrize("enabled", [True, False])
def test_a_failed_cron_change_keeps_the_saved_setting(auto_update_client, tmp_path, enabled):
    with CommonConfig.open(tmp_path) as config:
        config.updates.auto_pilot_update = not enabled
    with patch("pilot.core.server.auto_update.CronManager") as cron:
        action = cron.return_value.set_schedule if enabled else cron.return_value.remove_schedule
        action.side_effect = CronError("The crontab command is missing.")
        response = auto_update_client.patch("/api/v1/cli-auto-update", json={"enabled": enabled})
    assert response.status_code == 500
    assert "crontab" in response.get_json()["error"]["message"]
    assert CommonConfig.read(tmp_path).updates.auto_pilot_update is not enabled


def test_a_site_token_cannot_change_host_updates(auto_update_client, tmp_path):
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    auto_update_client.set_cookie("sid", Session(Bench(tmp_path / "current")).issue_site_token("test.local"))
    response = auto_update_client.patch("/api/v1/cli-auto-update", json={"enabled": True})
    assert response.status_code == 403
