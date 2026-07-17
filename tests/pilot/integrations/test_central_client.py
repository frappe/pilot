from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.commands.admin.set_central_config import SetCentralConfigCommand
from pilot.config.app_config import AppConfig
from pilot.config.bench_config import BenchConfig
from pilot.config.mariadb_config import MariaDBConfig
from pilot.config.redis_config import RedisConfig
from pilot.config.toml_store import BenchTomlStore
from pilot.config.worker_config import WorkerConfig, WorkerGroup
from pilot.core.bench import Bench
from pilot.integrations.central import CentralClient, CentralClientError
from pilot.exceptions import BenchError


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
    BenchTomlStore.for_bench(bench_dir).write(config)
    return bench


def _write_common(bench: Bench, data: dict) -> Path:
    path = bench.sites_path / "common_site_config.json"
    path.write_text(json.dumps(data))
    return path


def _write_central(bench: Bench, endpoint: str, token: str) -> None:
    store = BenchTomlStore.for_bench(bench.path)
    config = store.read_raw()
    config["central"] = {"endpoint": endpoint, "auth_token": token}
    store.write_raw(config)
    bench.config = store.read()


class _FakeResponse:
    def __init__(self, payload: dict) -> None:
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode()

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *exc) -> bool:
        return False


# --- set-central-config command --------------------------------------------


def test_set_central_config_merges_into_bench_toml(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    SetCentralConfigCommand(bench, endpoint="https://central.test", token="tok-123").run()
    config = BenchTomlStore.for_bench(bench.path).read_raw()
    assert config["central"]["endpoint"] == "https://central.test"
    assert config["central"]["auth_token"] == "tok-123"
    assert config["bench"]["name"] == "b1"  # untouched


def test_set_central_config_raises_without_bench_toml(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    (bench.path / "bench.toml").unlink()
    with pytest.raises(BenchError, match="not found"):
        SetCentralConfigCommand(bench, endpoint="https://central.test", token="tok").run()


# --- CentralClient ----------------------------------------------------------


def test_client_reads_and_strips_endpoint(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _write_central(bench, "https://central.test/", "tok")
    assert CentralClient(bench)._credentials() == ("https://central.test", "tok")


def test_client_raises_when_credentials_absent(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    with pytest.raises(CentralClientError, match="not set"):
        CentralClient(bench)._credentials()


def test_client_falls_back_to_legacy_common_site_config(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _write_common(bench, {"central_endpoint": "https://central.test/", "central_auth_token": "tok"})
    assert CentralClient(bench)._credentials() == ("https://central.test", "tok")


def test_heartbeat_sends_x_pilot_token_and_returns_echo(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _write_central(bench, "https://central.test/", "tok-9")
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["headers"] = dict(request.headers)
        return _FakeResponse({"ok": True, "team": "TEAM-1", "pilot_credential_id": "pcred-x"})

    with patch("pilot.integrations.central.urllib.request.urlopen", side_effect=fake_urlopen):
        result = CentralClient(bench).heartbeat()

    assert result["team"] == "TEAM-1"
    assert result["pilot_credential_id"] == "pcred-x"
    assert captured["url"] == "https://central.test/api/method/central.api.pilot.heartbeat"
    assert "tok-9" in captured["headers"].values()


def test_forward_unwraps_message_and_targets_method_path(tmp_path: Path) -> None:
	"""The generic forward hits /api/method/<path>, carries the X-Pilot-Token, and unwraps
	Frappe's {"message": ...} envelope — one code path for every Central pilot method."""
	bench = _bench(tmp_path)
	_write_central(bench, "https://central.test", "tok-7")
	captured: dict = {}

	def fake_urlopen(request, timeout=None):
		captured["url"] = request.full_url
		captured["method"] = request.method
		captured["body"] = request.data
		captured["headers"] = dict(request.headers)
		return _FakeResponse({"message": {"currency": "INR"}})

	with patch("pilot.core.central_client.urllib.request.urlopen", side_effect=fake_urlopen):
		result = CentralClient(bench).forward(
			"central.billing.api.billing_api.change_plan", "POST", {"plan": "biz"}
		)

	assert result == {"currency": "INR"}
	assert captured["url"] == "https://central.test/api/method/central.billing.api.billing_api.change_plan"
	assert captured["method"] == "POST"
	assert json.loads(captured["body"]) == {"plan": "biz"}
	assert "tok-7" in captured["headers"].values()


def test_heartbeat_wraps_non_json_response(tmp_path: Path) -> None:
    """A 2xx with a non-JSON body (e.g. a proxy's HTML error page) surfaces as a
    CentralClientError, not a bare JSONDecodeError."""
    bench = _bench(tmp_path)
    _write_central(bench, "https://central.test", "tok")

    class _HtmlResponse:
        def read(self) -> bytes:
            return b"<html><body>502 Bad Gateway</body></html>"

        def __enter__(self) -> "_HtmlResponse":
            return self

        def __exit__(self, *exc) -> bool:
            return False

    with patch("pilot.integrations.central.urllib.request.urlopen", return_value=_HtmlResponse()):
        with pytest.raises(CentralClientError):
            CentralClient(bench).heartbeat()


# --- central proxy view: allowlist + forward --------------------------------


def _app_client(bench_root: Path):
	from admin.backend.app import create_app
	from pilot.commands.generate_session import ensure_jwt_secret, issue_token
	from pilot.config.bench_toml_builder import BenchTomlBuilder

	bench_root.mkdir(parents=True, exist_ok=True)
	(bench_root / "bench.toml").write_text(
		BenchTomlBuilder(bench_root.name, {"admin_enabled": True, "admin_password": "secret"}).render()
	)
	secret = ensure_jwt_secret(bench_root / "bench.toml")
	app = create_app(bench_root)
	app.config["TESTING"] = True
	client = app.test_client()
	client.set_cookie("sid", issue_token(secret))
	return client


def test_proxy_forwards_allowlisted_billing_method(tmp_path: Path) -> None:
	client = _app_client(tmp_path / "bench")
	with patch(
		"admin.backend.views.central_proxy.CentralClient.forward", return_value={"currency": "INR"}
	) as fwd:
		resp = client.get("/api/sites/s1.localhost/central/central.billing.api.billing_api.get_billing_summary")

	assert resp.status_code == 200
	assert resp.get_json() == {"currency": "INR"}
	assert fwd.call_args.args[0] == "central.billing.api.billing_api.get_billing_summary"
	assert fwd.call_args.args[1] == "GET"


def test_proxy_rejects_non_allowlisted_method(tmp_path: Path) -> None:
	client = _app_client(tmp_path / "bench")
	with patch("admin.backend.views.central_proxy.CentralClient.forward") as fwd:
		resp = client.get("/api/sites/s1.localhost/central/central.api.teams.delete_team")

	assert resp.status_code == 403
	fwd.assert_not_called()
