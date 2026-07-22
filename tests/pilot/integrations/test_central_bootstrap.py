from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config.bench import BenchConfig
from pilot.integrations.central import CentralClientError, enroll_if_needed, seed, seed_from_metadata
from tests.pilot.integrations.test_central_client import _bench, _FakeResponse

_ENROLL_RESULT = {
    "auth_token": "pilot-token-abc",
    "central_endpoint": "https://central.test",
    "jwks_url": "https://central.test/api/method/central.api.jwks.get_jwks",
    "audience_id": "vm-boot-1",
}


def _seed(bench, *, endpoint="https://central.test", bootstrap_token="boot-xyz", auth_token=""):
    config = BenchConfig.read_raw(bench.path)
    central = config.setdefault("central", {})
    central["endpoint"] = endpoint
    if bootstrap_token:
        central["bootstrap_token"] = bootstrap_token
    if auth_token:
        central["auth_token"] = auth_token
    BenchConfig.write_raw(bench.path, config)
    bench.config.central.endpoint = endpoint
    bench.config.central.bootstrap_token = bootstrap_token
    bench.config.central.auth_token = auth_token


def test_enroll_exchanges_seed_and_persists_credential_and_jwks(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _seed(bench)
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["url"] = request.full_url
        captured["method"] = request.method
        captured["body"] = json.loads(request.data)
        captured["headers"] = dict(request.headers)
        return _FakeResponse({"message": _ENROLL_RESULT})

    with patch("pilot.integrations.central.bootstrap.urllib.request.urlopen", side_effect=fake_urlopen):
        enrolled = enroll_if_needed(bench)

    assert enrolled is True
    assert captured["url"] == "https://central.test/api/method/central.api.pilot.enroll"
    assert captured["method"] == "POST"
    assert captured["body"] == {"bootstrap_token": "boot-xyz"}
    assert "X-Pilot-Token" not in captured["headers"]

    saved = BenchConfig.read_raw(bench.path)
    assert saved["central"]["auth_token"] == "pilot-token-abc"
    assert "bootstrap_token" not in saved["central"]
    assert saved["admin"]["jwks_url"] == _ENROLL_RESULT["jwks_url"]
    assert saved["admin"]["jwks_audience"] == "vm-boot-1"

    assert bench.config.central.auth_token == "pilot-token-abc"
    assert bench.config.central.bootstrap_token == ""
    assert bench.config.admin.jwks_audience == "vm-boot-1"


def test_seed_then_enroll_from_scratch(tmp_path: Path) -> None:
    bench = _bench(tmp_path)  # no [central] section at all
    seed(bench, "https://central.test", "boot-xyz")
    assert bench.config.central.bootstrap_token == "boot-xyz"

    with patch(
        "pilot.integrations.central.bootstrap.urllib.request.urlopen",
        return_value=_FakeResponse({"message": _ENROLL_RESULT}),
    ):
        assert enroll_if_needed(bench) is True

    assert bench.config.central.auth_token == "pilot-token-abc"
    assert bench.config.admin.jwks_audience == "vm-boot-1"


def test_seed_from_metadata_stages_a_boot_time_seed(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    seed_path = tmp_path / "seed.json"
    seed_path.write_text(
        json.dumps({"central_endpoint": "https://central.test", "bootstrap_token": "boot-9"})
    )

    assert seed_from_metadata(bench, str(seed_path)) is True
    assert bench.config.central.endpoint == "https://central.test"
    assert bench.config.central.bootstrap_token == "boot-9"

    assert seed_from_metadata(bench, str(tmp_path / "absent.json")) is False


def test_bare_enroll_command_reads_the_canonical_seed_path(tmp_path: Path, monkeypatch) -> None:
    from pilot.commands.admin.enroll import EnrollCommand

    bench = _bench(tmp_path)
    seed_path = tmp_path / "central-seed.json"
    seed_path.write_text(
        json.dumps({"central_endpoint": "https://central.test", "bootstrap_token": "boot-boot"})
    )
    monkeypatch.setenv("PILOT_SEED_PATH", str(seed_path))

    with patch(
        "pilot.integrations.central.bootstrap.urllib.request.urlopen",
        return_value=_FakeResponse({"message": _ENROLL_RESULT}),
    ):
        EnrollCommand(bench=bench).run()

    assert bench.config.central.auth_token == "pilot-token-abc"
    assert bench.config.admin.jwks_audience == "vm-boot-1"


def test_enroll_is_a_noop_when_already_enrolled(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _seed(bench, bootstrap_token="", auth_token="already-have-one")

    with patch("pilot.integrations.central.bootstrap.urllib.request.urlopen") as urlopen:
        assert enroll_if_needed(bench) is False

    urlopen.assert_not_called()


def test_enroll_without_a_seed_raises(tmp_path: Path) -> None:
    bench = _bench(tmp_path)  # no [central] seed at all
    with pytest.raises(CentralClientError, match="bootstrap_token"):
        enroll_if_needed(bench)


def test_enroll_rejects_an_incomplete_response(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _seed(bench)
    incomplete = {"auth_token": "t"}  # no jwks_url / audience_id

    with (
        patch(
            "pilot.integrations.central.bootstrap.urllib.request.urlopen",
            return_value=_FakeResponse({"message": incomplete}),
        ),
        pytest.raises(CentralClientError, match="missing"),
    ):
        enroll_if_needed(bench)

    assert bench.config.central.auth_token == ""
