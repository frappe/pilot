from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.commands.admin.set_central_config import SetCentralConfigCommand
from pilot.config.app import AppConfig
from pilot.config.bench import BenchConfig
from pilot.config.mariadb import MariaDBConfig
from pilot.config.redis import RedisConfig
from pilot.config.toml_store import BenchTomlStore
from pilot.config.worker import WorkerConfig, WorkerGroup
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
