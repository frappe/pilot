"""Tests for GET /api/v1/cli-updates and POST /api/v1/cli-update-checks."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from pilot.config import BenchConfig


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


def test_cli_updates_shows_the_update_channel_only_under_central(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with patch("pilot.is_dev_build", False):
        assert client.get("/api/v1/cli-updates").get_json()["update_channel"] is None

        with BenchConfig.open(bench_root) as config:
            config.central.enabled = True
            config.central.update_channel = "late"
        assert client.get("/api/v1/cli-updates").get_json()["update_channel"] == "late"
