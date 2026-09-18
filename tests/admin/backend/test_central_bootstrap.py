from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from flask import Flask

from admin.backend.central_bootstrap import (
    CentralBootstrapWatcher,
    install_central_bootstrap_watcher,
    main,
)
from admin.backend.internal.jwks_cache import JwksCache
from pilot.config import BenchConfig
from pilot.config.common import CommonConfig
from pilot.exceptions import ConfigError
from pilot.integrations.central import CentralClientError
from tests.admin.backend.test_jwks import _jwks_document
from tests.pilot.integrations.test_central_client import _bench
from tests.pilot.integrations.test_central_metadata import _ATTRIBUTE


def _awaiting_host(tmp_path: Path) -> Path:
    bench = _bench(tmp_path)
    common = CommonConfig.read(bench.path.parent)
    common.central.enabled = True
    common.write(bench.path.parent)
    return bench.path


def _staged(value: str | None):
    """The raw attribute, so the real parsing still runs."""
    return patch(
        "pilot.integrations.central.metadata.InstanceMetadata.get_attribute",
        return_value=value,
    )


def test_a_pass_without_the_attribute_keeps_waiting(tmp_path: Path) -> None:
    watcher = CentralBootstrapWatcher(_awaiting_host(tmp_path))

    with _staged(None):
        assert watcher.check_once() is False


def test_the_attribute_arriving_writes_the_config(tmp_path: Path) -> None:
    bench_root = _awaiting_host(tmp_path)

    with _staged(json.dumps(_ATTRIBUTE)):
        assert CentralBootstrapWatcher(bench_root).check_once() is True

    saved = BenchConfig.read(bench_root)
    assert saved.central.bootstrapped is True
    assert saved.admin.jwks_audience == "vm-boot-1"


def test_bootstrap_seeds_the_jwks_cache_so_the_first_token_needs_no_fetch(tmp_path: Path, monkeypatch) -> None:
    from jwt import PyJWKClient
    from jwt.exceptions import PyJWKClientError

    bench_root = _awaiting_host(tmp_path)
    attribute = {**_ATTRIBUTE, "initial_jwks_cache": _jwks_document()}

    with _staged(json.dumps(attribute)):
        assert CentralBootstrapWatcher(bench_root).check_once() is True

    def refuse(client):
        raise PyJWKClientError("issuer unreachable")

    monkeypatch.setattr(PyJWKClient, "fetch_data", refuse)
    assert JwksCache(bench_root.parent, _ATTRIBUTE["jwks_url"]).signing_key("rsa-key") is not None


def test_an_unusable_initial_jwks_cache_does_not_stop_bootstrap(tmp_path: Path) -> None:
    bench_root = _awaiting_host(tmp_path)
    attribute = {**_ATTRIBUTE, "initial_jwks_cache": {"keys": []}}

    with _staged(json.dumps(attribute)):
        assert CentralBootstrapWatcher(bench_root).check_once() is True

    assert CommonConfig.read(bench_root.parent).central.bootstrapped is True
    assert not (bench_root.parent / JwksCache.FILENAME).exists()


def test_a_malformed_attribute_is_logged_and_retried(tmp_path: Path) -> None:
    watcher = CentralBootstrapWatcher(_awaiting_host(tmp_path))

    with patch(
        "pilot.integrations.central.metadata.InstanceMetadata.get_credentials",
        side_effect=CentralClientError("bad attribute"),
    ):
        assert watcher.check_once() is False


def test_the_watcher_starts_only_while_a_host_is_awaiting_bootstrap(tmp_path: Path) -> None:
    bench_root = _awaiting_host(tmp_path)
    app = Flask(__name__)

    with patch.object(CentralBootstrapWatcher, "install") as install:
        assert install_central_bootstrap_watcher(app, bench_root) is not None
    install.assert_called_once()


def test_the_watcher_does_not_start_on_a_bootstrapped_host(tmp_path: Path) -> None:
    bench_root = _awaiting_host(tmp_path)
    common = CommonConfig.read(bench_root.parent)
    common.central.bootstrapped = True
    common.write(bench_root.parent)

    assert install_central_bootstrap_watcher(Flask(__name__), bench_root) is None


def test_the_watcher_does_not_start_on_a_self_hosted_bench(tmp_path: Path) -> None:
    bench = _bench(tmp_path)  # central.enabled is False

    assert install_central_bootstrap_watcher(Flask(__name__), bench.path) is None


def test_bootstrap_reports_pending_while_the_host_waits(tmp_path: Path) -> None:
    from admin.backend.app import create_app

    bench_root = _awaiting_host(tmp_path)
    body = create_app(bench_root).test_client().get("/api/v1/bootstrap").get_json()

    assert body == {"mode": "pending", "name": bench_root.name, "enabled": True}


def test_bootstrap_leaves_pending_once_the_credential_lands(tmp_path: Path) -> None:
    from admin.backend.app import create_app

    bench_root = _awaiting_host(tmp_path)
    with _staged(json.dumps(_ATTRIBUTE)):
        CentralBootstrapWatcher(bench_root).check_once()

    body = create_app(bench_root).test_client().get("/api/v1/bootstrap").get_json()

    assert body["mode"] != "pending"


def test_bootstrap_keeps_a_shared_setting_committed_while_it_waited(tmp_path: Path) -> None:
    """Every field bootstrap writes is host-shared, so the write has to go
    through the shared file's own lock rather than a snapshot read earlier."""
    from pilot.config.common import CommonConfig
    from pilot.core.bench import Bench
    from pilot.integrations.central import apply_central_config

    bench_root = _awaiting_host(tmp_path)
    bench = Bench(bench_root)  # holds a view of the shared file from now

    with CommonConfig.open(bench_root.parent) as concurrent:
        concurrent.datum.endpoint = "https://datum.committed-later"

    with _staged(json.dumps(_ATTRIBUTE)):
        assert apply_central_config(bench) is True

    saved = CommonConfig.read(bench_root.parent)
    assert saved.central.bootstrapped is True
    assert saved.datum.endpoint == "https://datum.committed-later"


def test_only_one_bootstrap_takes_effect(tmp_path: Path) -> None:
    """Two watchers racing must not both claim to have configured the host."""
    from pilot.core.bench import Bench
    from pilot.integrations.central import apply_central_config

    bench_root = _awaiting_host(tmp_path)
    first, second = Bench(bench_root), Bench(bench_root)

    with _staged(json.dumps(_ATTRIBUTE)):
        assert apply_central_config(first) is True
        assert apply_central_config(second) is False


def test_the_poll_interval_never_grows(tmp_path: Path) -> None:
    """A host waiting on its credential is a signup waiting on it, so the watcher
    keeps checking at one short interval rather than backing off."""
    watcher = CentralBootstrapWatcher(_awaiting_host(tmp_path), interval=0.01)
    attempts = iter([False, False, False, True])
    slept: list[float] = []

    with patch.object(CentralBootstrapWatcher, "check_once", lambda self: next(attempts)), patch(
        "admin.backend.central_bootstrap.time.sleep", slept.append
    ):
        watcher._watch()

    assert slept == [0.01, 0.01, 0.01]


def test_the_watcher_stops_as_soon_as_the_credential_lands(tmp_path: Path) -> None:
    watcher = CentralBootstrapWatcher(_awaiting_host(tmp_path), interval=0.01)
    slept: list[float] = []

    with patch.object(CentralBootstrapWatcher, "check_once", lambda self: True), patch(
        "admin.backend.central_bootstrap.time.sleep", slept.append
    ):
        watcher._watch()

    assert slept == []


def test_an_unrelated_validation_problem_does_not_stop_bootstrap(tmp_path: Path) -> None:
    """Only central.enabled decides this, so a config that is invalid elsewhere
    must not be what leaves a Central-managed host unconfigured forever."""
    bench_root = _awaiting_host(tmp_path)
    # An admin domain that is not a hostname: the file parses, validation fails.
    toml_path = bench_root / "bench.toml"
    toml_path.write_text(
        toml_path.read_text(encoding="utf-8").replace('domain = ""', 'domain = "not a hostname"'),
        encoding="utf-8",
    )

    with pytest.raises(ConfigError):
        BenchConfig.read(bench_root)

    with patch.object(CentralBootstrapWatcher, "install") as install:
        assert install_central_bootstrap_watcher(Flask(__name__), bench_root) is not None
    install.assert_called_once()

    # Starting the watch is not enough: the check itself has to survive the same
    # config, or the thread dies and the host is never bootstrapped.
    with _staged(json.dumps(_ATTRIBUTE)):
        assert CentralBootstrapWatcher(bench_root).check_once() is True
    assert CommonConfig.read(bench_root.parent).central.bootstrapped is True


def test_an_unexpected_failure_keeps_the_watch_alive(tmp_path: Path) -> None:
    """The credential, and any config it trips over, can still be fixed in place."""
    watcher = CentralBootstrapWatcher(_awaiting_host(tmp_path))

    with patch(
        "pilot.integrations.central.apply_central_config", side_effect=RuntimeError("disk gone")
    ):
        assert watcher.check_once() is False


def test_a_config_that_cannot_be_read_at_all_is_reported(tmp_path: Path, caplog) -> None:
    bench_root = _awaiting_host(tmp_path)
    (bench_root / "bench.toml").write_text("{{{ not toml", encoding="utf-8")

    with caplog.at_level("ERROR"):
        assert install_central_bootstrap_watcher(Flask(__name__), bench_root) is None

    assert "awaiting a Central credential" in caplog.text


def test_boot_run_applies_the_credential(tmp_path: Path) -> None:
    bench_root = _awaiting_host(tmp_path)

    with _staged(json.dumps(_ATTRIBUTE)), patch("sys.argv", ["central_bootstrap", "--bench-root", str(bench_root)]):
        main()

    assert BenchConfig.read(bench_root).central.bootstrapped is True


def test_boot_run_skips_non_central_hosts(tmp_path: Path) -> None:
    bench = _bench(tmp_path)

    with _staged(None), patch("sys.argv", ["central_bootstrap", "--bench-root", str(bench.path)]):
        main()

    assert BenchConfig.read(bench.path).central.bootstrapped is False
