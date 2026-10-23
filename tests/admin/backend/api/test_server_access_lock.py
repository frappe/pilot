import json
from unittest.mock import Mock, patch

import pytest

from admin.backend.api.responses import error_response
from admin.backend.api.v1.sites.shared import server_access_locked_response
from pilot.integrations.central.client import CentralClientError
from tests.admin.backend.test_admin_app import _client

CREDENTIALS = {"central_endpoint": "https://central.example", "central_auth_token": "token"}


@pytest.fixture
def site_request(tmp_path):
    """A request that holds a single site's token on a server that Central manages."""
    client = _client(tmp_path / "benches" / "current")
    with (
        client.application.test_request_context(),
        patch("admin.backend.middleware.is_bench_scoped", return_value=False),
        patch("pilot.core.bench.Bench") as bench,
        patch(
            "pilot.integrations.central.metadata.InstanceMetadata.get_credentials",
            return_value=CREDENTIALS,
        ),
    ):
        bench.return_value.config.central.enabled = True
        yield bench


def _locked(value):
    return patch(
        "pilot.integrations.central.client.CentralClient.is_server_access_locked",
        **({"side_effect": value} if isinstance(value, Exception) else {"return_value": value}),
    )


def test_a_locked_server_refuses_a_site_token(site_request) -> None:
    with _locked(True):
        body, status = server_access_locked_response()

    assert status == 403
    assert body.get_json()["error"]["code"] == "upgrade_required"


def test_an_unlocked_server_allows_a_site_token(site_request) -> None:
    with _locked(False):
        assert server_access_locked_response() is None


def test_an_unreachable_central_keeps_the_request_waiting(site_request) -> None:
    with _locked(CentralClientError("down")):
        _, status = server_access_locked_response()

    assert status == 503


def test_a_bench_session_is_never_locked(site_request) -> None:
    with patch("admin.backend.middleware.is_bench_scoped", return_value=True), _locked(True):
        assert server_access_locked_response() is None


def test_a_server_without_central_is_never_locked(site_request) -> None:
    site_request.return_value.config.central.enabled = False

    with _locked(True):
        assert server_access_locked_response() is None


def test_a_central_server_that_cannot_read_its_credential_keeps_the_request_waiting(site_request) -> None:
    with patch("pilot.integrations.central.metadata.InstanceMetadata.get_credentials", return_value=None):
        _, status = server_access_locked_response()

    assert status == 503


def _refused():
    return error_response("upgrade_required", "Not in the free trial. Upgrade to use it.", 403)


def test_a_locked_server_keeps_its_site_config_unchanged(tmp_path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    config_path = bench_root / "sites" / "s.localhost" / "site_config.json"
    config_path.parent.mkdir(parents=True)
    config_path.write_text(json.dumps({"installed_apps": [], "developer_mode": 0}))

    with patch("admin.backend.api.v1.sites.configuration.server_access_locked_response", _refused):
        response = client.patch("/api/v1/sites/s.localhost/configuration", json={"redis_cache": "x"})

    assert response.status_code == 403
    assert json.loads(config_path.read_text()) == {"installed_apps": [], "developer_mode": 0}


def test_a_locked_server_refuses_the_config_backup_file(tmp_path) -> None:
    client = _client(tmp_path / "benches" / "current")

    with patch("admin.backend.api.v1.sites.backups.server_access_locked_response", _refused):
        response = client.get(
            "/api/v1/sites/s.localhost/backups/20261022/files/20261022-s-site_config_backup.json/content"
        )

    assert response.status_code == 403


def test_a_central_that_stalls_keeps_the_request_waiting(site_request) -> None:
    with patch("urllib.request.urlopen", side_effect=TimeoutError("read timed out")):
        _, status = server_access_locked_response()

    assert status == 503


def test_a_locked_server_leaves_the_config_out_of_offsite_download_links(tmp_path) -> None:
    from tests.admin.backend.api.test_site_backups_view import _make_site

    bench_root = tmp_path / "benches" / "current"
    _make_site(bench_root, "site.localhost")
    client = _client(bench_root)
    offsite = Mock()
    offsite.get_backup.return_value = {
        "database": "20261022_000000-site-database.sql.gz",
        "site_config": "20261022_000000-site-site_config_backup.json",
    }
    offsite.presigned_url.return_value = "https://bucket.example/signed"

    with (
        patch("pilot.integrations.s3.backups.OffsiteBackup.from_config", return_value=offsite),
        patch("admin.backend.api.v1.sites.backups.server_access_locked_response", _refused),
    ):
        response = client.get("/api/v1/sites/site.localhost/backups/20261022_000000/download-links")

    assert response.status_code == 200
    assert response.get_json() == {"database": "https://bucket.example/signed"}
