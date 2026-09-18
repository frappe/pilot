from __future__ import annotations

import io
import json
import urllib.error
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import (
    AppConfig,
    BenchConfig,
    MariaDBConfig,
    RedisConfig,
    WorkerConfig,
    WorkerGroup,
)
from pilot.core.bench import Bench
from pilot.integrations.central import CentralClient, CentralClientError


def _bench(root: Path, name: str = "b1") -> Bench:
    bench_dir = root / "benches" / name
    bench_dir.mkdir(parents=True, exist_ok=True)
    config = BenchConfig(
        name=name,
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[WorkerGroup(queues=["default"], count=1)]),
    )
    bench = Bench(config, bench_dir)
    bench.create_directories()
    config.write(bench_dir)
    return bench


_STAGED: dict[str, str] = {}


@pytest.fixture(autouse=True)
def staged_metadata():
    """Metadata owns the endpoint and token, so every test stands one in."""
    _STAGED.clear()
    with patch(
        "pilot.integrations.central.metadata.InstanceMetadata.get_credentials",
        side_effect=lambda *_, **__: dict(_STAGED) or None,
    ):
        yield


def _stage_credentials(endpoint: str, token: str) -> None:
    _STAGED.update(
        central_endpoint=endpoint,
        central_auth_token=token,
        jwks_url="https://central.test/jwks",
        jwks_audience_id="vm-1",
    )


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


def test_client_reads_and_strips_endpoint() -> None:
    _stage_credentials("https://central.test/", "tok")
    assert CentralClient()._credentials() == ("https://central.test", "tok")


def test_client_raises_when_metadata_has_no_credential() -> None:
    with pytest.raises(CentralClientError, match="instance metadata"):
        CentralClient()._credentials()


def test_an_unwrapped_response_comes_back_whole() -> None:
    _stage_credentials("https://central.test/", "tok-9")
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        return _FakeResponse({"ok": True, "team": "TEAM-1"})

    with patch("pilot.integrations.central.client.urllib.request.urlopen", side_effect=fake_urlopen):
        result = CentralClient().forward("central.api.pilot.heartbeat", "GET")

    assert result == {"ok": True, "team": "TEAM-1"}
    assert captured["url"] == "https://central.test/api/method/central.api.pilot.heartbeat"
    assert "tok-9" in captured["headers"].values()


def test_forward_unwraps_message_and_targets_method_path() -> None:
    _stage_credentials("https://central.test", "tok-7")
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["body"] = request.data
        captured["headers"] = dict(request.headers)
        return _FakeResponse({"message": {"currency": "INR"}})

    with patch("pilot.integrations.central.client.urllib.request.urlopen", side_effect=fake_urlopen):
        result = CentralClient().forward(
            "central.billing.api.billing_api.change_plan", "POST", {"plan": "biz"}
        )

    assert result == {"currency": "INR"}
    assert captured["url"] == "https://central.test/api/method/central.billing.api.billing_api.change_plan"
    assert captured["method"] == "POST"
    assert json.loads(captured["body"]) == {"plan": "biz"}
    assert "tok-7" in captured["headers"].values()


def test_log_token_unwraps_message_and_targets_method_path() -> None:
    _stage_credentials("https://central.test", "tok-8")
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.method
        return _FakeResponse(
            {"message": {"token": "jwt-123", "expires_in": 604800, "resource_id": "vm-1"}}
        )

    with patch("pilot.integrations.central.client.urllib.request.urlopen", side_effect=fake_urlopen):
        result = CentralClient().log_token()

    assert result["token"] == "jwt-123"
    assert result["resource_id"] == "vm-1"
    assert captured["url"] == "https://central.test/api/method/central.api.pilot.log_token"
    assert captured["method"] == "GET"


def test_a_non_json_response_is_wrapped() -> None:
    _stage_credentials("https://central.test", "tok")

    class _HtmlResponse:
        def read(self) -> bytes:
            return b"<html><body>502 Bad Gateway</body></html>"

        def __enter__(self) -> "_HtmlResponse":
            return self

        def __exit__(self, *exc) -> bool:
            return False

    with (
        patch("pilot.integrations.central.client.urllib.request.urlopen", return_value=_HtmlResponse()),
        pytest.raises(CentralClientError),
    ):
        CentralClient().forward("central.api.pilot.heartbeat", "GET")


def _app_client(bench_root: Path):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(bench_root.name, {"admin_enabled": True, "admin_password": "secret"}).dumps()
    )
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return client


def test_proxy_forwards_allowlisted_billing_method(tmp_path: Path) -> None:
    client = _app_client(tmp_path / "bench")
    with patch(
        "admin.backend.api.v1.sites.central.CentralClient.forward",
        return_value={"currency": "INR"},
    ) as forward:
        response = client.get(
            "/api/v1/sites/s1.localhost/central/central.billing.api.billing_api.get_billing_summary"
        )

    assert response.status_code == 200
    assert response.get_json() == {"currency": "INR"}
    assert forward.call_args.args[0] == "central.billing.api.billing_api.get_billing_summary"
    assert forward.call_args.args[1] == "GET"


def test_proxy_rejects_non_allowlisted_method(tmp_path: Path) -> None:
    client = _app_client(tmp_path / "bench")
    with patch("admin.backend.api.v1.sites.central.CentralClient.forward") as forward:
        response = client.get("/api/v1/sites/s1.localhost/central/central.api.teams.delete_team")

    assert response.status_code == 403
    forward.assert_not_called()


def test_account_url_returns_the_endpoint_from_metadata(tmp_path: Path) -> None:
    client = _app_client(tmp_path / "bench")
    _stage_credentials("https://central.test/", "tok")

    response = client.get("/api/v1/sites/s1.localhost/account-url")

    assert response.status_code == 200
    assert response.get_json() == {"url": "https://central.test"}


def test_account_url_requires_a_staged_credential(tmp_path: Path) -> None:
    client = _app_client(tmp_path / "bench")

    response = client.get("/api/v1/sites/s1.localhost/account-url")

    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "central_not_configured"


def _http_error(code: int, body: bytes) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://central.test/x", code, "err", {}, io.BytesIO(body))


def test_rejection_surfaces_centrals_own_message() -> None:
    """Central validates billing input; reporting only the status code would strand
    the user with an unactionable "HTTP 417"."""
    _stage_credentials("https://central.test", "tok")
    body = json.dumps(
        {
            "exception": "frappe.exceptions.ValidationError: 'MH' is not a recognised Indian state.",
            "_server_messages": json.dumps(
                [json.dumps({"message": "'MH' is not a recognised Indian state."})]
            ),
        }
    ).encode()

    with (
        patch(
            "pilot.integrations.central.client.urllib.request.urlopen",
            side_effect=_http_error(417, body),
        ),
        pytest.raises(CentralClientError) as excinfo,
    ):
        CentralClient().forward("central.billing.api.billing_api.save_billing_profile", "POST", {})

    assert str(excinfo.value) == "'MH' is not a recognised Indian state."
    assert excinfo.value.status_code == 417


def test_rejection_without_a_message_falls_back_to_the_status() -> None:
    _stage_credentials("https://central.test", "tok")

    with (
        patch(
            "pilot.integrations.central.client.urllib.request.urlopen",
            side_effect=_http_error(403, b"<html>nope</html>"),
        ),
        pytest.raises(CentralClientError) as excinfo,
    ):
        CentralClient().forward("central.billing.api.billing_api.save_billing_profile", "POST", {})

    assert "HTTP 403" in str(excinfo.value)
    assert excinfo.value.status_code == 403


def test_unreachable_central_has_no_status_code() -> None:
    _stage_credentials("https://central.test", "tok")

    with (
        patch(
            "pilot.integrations.central.client.urllib.request.urlopen",
            side_effect=urllib.error.URLError("connection refused"),
        ),
        pytest.raises(CentralClientError) as excinfo,
    ):
        CentralClient().forward("central.api.pilot.heartbeat", "GET")

    assert excinfo.value.status_code is None


def test_proxy_relays_a_central_rejection_instead_of_a_502(tmp_path: Path) -> None:
    """A rejection is the caller's problem to show; only an outage is a 502."""
    client = _app_client(tmp_path / "bench")
    with patch(
        "admin.backend.api.v1.sites.central.CentralClient.forward",
        side_effect=CentralClientError("Pick a state from the list.", status_code=417),
    ):
        response = client.post(
            "/api/v1/sites/s1.localhost/central/central.billing.api.billing_api.save_billing_profile",
            json={"state": "MH"},
        )

    assert response.status_code == 417
    body = response.get_json()["error"]
    assert body["code"] == "central_rejected"
    assert body["message"] == "Pick a state from the list."


def test_proxy_still_reports_an_outage_as_unreachable(tmp_path: Path) -> None:
    client = _app_client(tmp_path / "bench")
    with patch(
        "admin.backend.api.v1.sites.central.CentralClient.forward",
        side_effect=CentralClientError("Cannot reach Central"),
    ):
        response = client.post(
            "/api/v1/sites/s1.localhost/central/central.billing.api.billing_api.save_billing_profile",
            json={},
        )

    assert response.status_code == 502
    assert response.get_json()["error"]["code"] == "central_unreachable"
