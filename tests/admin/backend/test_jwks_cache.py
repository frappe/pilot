from __future__ import annotations

import json
import time

import pytest
from jwt import PyJWKClient
from jwt.exceptions import PyJWKClientError

from admin.backend.internal.jwks_cache import JwksCache
from tests.admin.backend.test_jwks import JWKS_URL, _jwks_document

OTHER_ISSUER = "https://other-issuer.example.com/jwks.json"


@pytest.fixture(autouse=True)
def _fresh_state():
    JwksCache._refreshing.clear()
    JwksCache._last_forced_fetch.clear()


@pytest.fixture
def fetches(monkeypatch) -> list[str]:
    """The URL of every fetch the cache makes, each answered with the issuer's key set."""
    calls: list[str] = []

    def fetch(client):
        calls.append(client.uri)
        return _jwks_document()

    monkeypatch.setattr(PyJWKClient, "fetch_data", fetch)
    return calls


def _refuse_fetches(monkeypatch) -> None:
    def fetch(client):
        raise PyJWKClientError("issuer unreachable")

    monkeypatch.setattr(PyJWKClient, "fetch_data", fetch)


def _seeded(tmp_path, fetched_at: int | None = None) -> JwksCache:
    cache = JwksCache(tmp_path, JWKS_URL)
    assert cache.seed(_jwks_document())
    if fetched_at is not None:
        record = json.loads(cache.path.read_text())
        record["fetched_at"] = fetched_at
        cache.path.write_text(json.dumps(record))
    return cache


def _record_background_refreshes(monkeypatch) -> list:
    started: list = []
    monkeypatch.setattr(JwksCache, "refresh_in_background", lambda self: started.append(self.path))
    return started


def test_a_cached_key_needs_no_fetch(tmp_path, monkeypatch) -> None:
    cache = _seeded(tmp_path)
    _refuse_fetches(monkeypatch)

    assert cache.signing_key("rsa-key") is not None


def test_a_fresh_cache_is_not_refreshed(tmp_path, monkeypatch) -> None:
    cache = _seeded(tmp_path)
    started = _record_background_refreshes(monkeypatch)

    cache.signing_key("rsa-key")

    assert started == []


def test_a_stale_cache_answers_at_once_and_refreshes_in_the_background(tmp_path, monkeypatch) -> None:
    cache = _seeded(tmp_path, fetched_at=int(time.time()) - JwksCache.REFRESH_AFTER_SECONDS - 1)
    started = _record_background_refreshes(monkeypatch)

    assert cache.signing_key("rsa-key") is not None
    assert started == [cache.path]


def test_only_one_background_refresh_runs_at_a_time(tmp_path, monkeypatch) -> None:
    threads: list = []

    class _Thread:
        def __init__(self, target, name, daemon) -> None:
            threads.append(target)

        def start(self) -> None:
            pass

    monkeypatch.setattr("admin.backend.internal.jwks_cache.threading.Thread", _Thread)
    cache = JwksCache(tmp_path, JWKS_URL)

    cache.refresh_in_background()
    cache.refresh_in_background()

    assert len(threads) == 1


def test_an_unknown_kid_forces_a_fetch_and_stores_the_result(tmp_path, fetches) -> None:
    cache = JwksCache(tmp_path, JWKS_URL)

    assert cache.signing_key("rsa-key") is not None
    assert fetches == [JWKS_URL]
    assert cache.path.exists()


def test_forced_fetches_are_limited_to_one_per_interval(tmp_path, fetches) -> None:
    cache = JwksCache(tmp_path, JWKS_URL)

    assert cache.signing_key("rotated-away") is None
    assert cache.signing_key("rotated-away") is None
    assert fetches == [JWKS_URL]


def test_a_failed_fetch_keeps_the_cached_keys(tmp_path, monkeypatch) -> None:
    cache = _seeded(tmp_path)
    _refuse_fetches(monkeypatch)

    assert cache.refresh() is None
    assert cache.signing_key("rsa-key") is not None


def test_a_key_set_without_usable_keys_is_not_stored(tmp_path, monkeypatch) -> None:
    cache = _seeded(tmp_path)
    monkeypatch.setattr(PyJWKClient, "fetch_data", lambda client: {"keys": []})

    assert cache.refresh() is None
    assert cache.signing_key("rsa-key") is not None


def test_a_cache_from_another_issuer_is_not_trusted(tmp_path, fetches) -> None:
    _seeded(tmp_path)

    JwksCache(tmp_path, OTHER_ISSUER).signing_key("rsa-key")

    assert fetches == [OTHER_ISSUER]


def test_an_unusable_seed_is_not_stored(tmp_path) -> None:
    cache = JwksCache(tmp_path, JWKS_URL)

    assert cache.seed({"keys": []}) is False
    assert not cache.path.exists()
