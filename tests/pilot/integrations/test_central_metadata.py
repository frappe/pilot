from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config.bench import BenchConfig
from pilot.integrations.central import CentralClientError, InstanceMetadata, apply_central_config
from tests.pilot.integrations.test_central_client import _bench

_ATTRIBUTE = {
    "central_endpoint": "https://central.test",
    "central_auth_token": "pilot-token-abc",
    "jwks_url": "https://central.test/api/method/central.api.jwks.get_jwks",
    "jwks_audience_id": "vm-boot-1",
}

# Pilot only checks the shape; the admin validates the keys.
_KEY_SET = {"keys": [{"kid": "atlas-key"}]}


class _FakeMetadata(InstanceMetadata):
    """The metadata service. A None value means the attribute is unset."""

    def __init__(self, value: str | None) -> None:
        super().__init__()
        self.value = value
        self.requested: list[str] = []

    def get_attribute(self, name: str) -> str | None:
        self.requested.append(name)
        return self.value


def test_credentials_come_back_from_the_configured_attribute(monkeypatch) -> None:
    monkeypatch.setenv("PILOT_METADATA_KEY", "my-key")
    metadata = _FakeMetadata(json.dumps(_ATTRIBUTE))

    assert metadata.get_credentials() == _ATTRIBUTE
    assert metadata.requested == ["my-key"]


def test_an_unset_attribute_is_not_an_error() -> None:
    assert _FakeMetadata(None).get_credentials() is None


def _awaiting_bench(tmp_path: Path):
    """A host Central manages but has not yet given a credential to.

    `central.enabled` is host-shared, so it has to be on disk and not only on
    this object: bootstrap re-reads it while holding the shared file's lock, so
    that two watchers cannot both apply the credential.
    """
    from pilot.config.common import CommonConfig

    bench = _bench(tmp_path)
    with CommonConfig.open(bench.path.parent) as common:
        common.central.enabled = True
    bench.config.central.enabled = True
    return bench


def test_apply_marks_the_host_bootstrapped_and_saves_the_jwks_issuer(tmp_path: Path) -> None:
    bench = _awaiting_bench(tmp_path)

    assert apply_central_config(bench, _FakeMetadata(json.dumps(_ATTRIBUTE))) is True

    assert bench.config.central.bootstrapped is True
    assert bench.config.admin.jwks_url == _ATTRIBUTE["jwks_url"]
    assert bench.config.admin.jwks_audience == "vm-boot-1"

    # Host-shared, so it lands in common_config.toml.
    saved = BenchConfig.read(bench.path)
    assert saved.central.bootstrapped is True
    assert saved.admin.jwks_audience == "vm-boot-1"


def test_the_endpoint_and_token_are_never_persisted(tmp_path: Path) -> None:
    bench = _awaiting_bench(tmp_path)

    apply_central_config(bench, _FakeMetadata(json.dumps(_ATTRIBUTE)))

    saved = (bench.path.parent / "common_config.toml").read_text()
    assert "pilot-token-abc" not in saved
    assert "central_auth_token" not in saved
    assert "endpoint" not in saved.split("[datum]")[0]


def test_apply_waits_while_the_cloud_has_not_published_yet(tmp_path: Path) -> None:
    bench = _awaiting_bench(tmp_path)

    assert apply_central_config(bench, _FakeMetadata(None)) is False
    assert bench.config.central.bootstrapped is False


def test_apply_skips_a_host_that_is_not_central_managed(tmp_path: Path) -> None:
    bench = _bench(tmp_path)  # central.enabled is False
    metadata = _FakeMetadata(json.dumps(_ATTRIBUTE))

    assert apply_central_config(bench, metadata) is False
    assert metadata.requested == []


def test_apply_leaves_an_already_bootstrapped_host_alone(tmp_path: Path) -> None:
    bench = _awaiting_bench(tmp_path)
    bench.config.central.bootstrapped = True
    metadata = _FakeMetadata(json.dumps(_ATTRIBUTE))

    assert apply_central_config(bench, metadata) is False
    assert metadata.requested == []


def test_an_incomplete_attribute_raises(tmp_path: Path) -> None:
    bench = _awaiting_bench(tmp_path)
    incomplete = json.dumps({"central_auth_token": "t"})  # no endpoint / jwks_url / audience

    with pytest.raises(CentralClientError, match="missing"):
        apply_central_config(bench, _FakeMetadata(incomplete))

    assert bench.config.central.bootstrapped is False


def test_the_initial_jwks_cache_comes_back_with_the_credentials() -> None:
    attribute = {**_ATTRIBUTE, "initial_jwks_cache": _KEY_SET}

    credentials = _FakeMetadata(json.dumps(attribute)).get_credentials()

    assert credentials["initial_jwks_cache"] == _KEY_SET


def test_an_initial_jwks_cache_that_is_not_an_object_raises() -> None:
    malformed = json.dumps({**_ATTRIBUTE, "initial_jwks_cache": "keys"})

    with pytest.raises(CentralClientError, match="initial_jwks_cache"):
        _FakeMetadata(malformed).get_credentials()


def test_apply_hands_over_the_credentials_before_the_host_reads_as_bootstrapped(tmp_path: Path) -> None:
    from pilot.config.common import CommonConfig

    bench = _awaiting_bench(tmp_path)
    seen: list[tuple[dict, bool]] = []

    def on_credentials(credentials) -> None:
        seen.append((credentials["initial_jwks_cache"], CommonConfig.read(bench.path.parent).central.bootstrapped))

    attribute = {**_ATTRIBUTE, "initial_jwks_cache": _KEY_SET}
    assert apply_central_config(bench, _FakeMetadata(json.dumps(attribute)), on_credentials) is True

    assert seen == [(_KEY_SET, False)]


def test_a_metadata_flavoured_endpoint_is_rejected() -> None:
    hostile = json.dumps({**_ATTRIBUTE, "central_endpoint": "http://169.254.169.254/"})

    with pytest.raises(CentralClientError):
        _FakeMetadata(hostile).get_credentials()


def test_a_non_json_attribute_raises() -> None:
    with pytest.raises(CentralClientError, match="not JSON"):
        _FakeMetadata("not json at all").get_credentials()


def test_the_token_exchange_uses_the_metadata_headers() -> None:
    calls: list[tuple[str, str, dict]] = []

    class _Response:
        def __init__(self, body: str) -> None:
            self.body = body

        def read(self) -> bytes:
            return self.body.encode()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    def fake_urlopen(request, timeout=None):
        calls.append((request.method, request.full_url, dict(request.headers)))
        if request.full_url.endswith("/api/token"):
            return _Response("imds-token")
        return _Response(json.dumps(_ATTRIBUTE))

    with patch("pilot.integrations.central.metadata.urllib.request.urlopen", side_effect=fake_urlopen):
        value = InstanceMetadata().get_attribute("my-key")

    assert json.loads(value) == _ATTRIBUTE
    token_call, attribute_call = calls
    assert token_call[0] == "PUT"
    assert token_call[1] == "http://169.254.169.254/latest/api/token"
    assert token_call[2]["X-metadata-token-ttl-seconds"] == "21600"
    assert attribute_call[1] == "http://169.254.169.254/latest/meta-data/attributes/my-key"
    assert attribute_call[2]["X-metadata-token"] == "imds-token"


def test_an_unreachable_metadata_service_is_not_an_error() -> None:
    with patch(
        "pilot.integrations.central.metadata.urllib.request.urlopen",
        side_effect=OSError("no route to host"),
    ):
        assert InstanceMetadata(timeout=0.1).get_attribute("my-key") is None
