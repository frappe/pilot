"""The one Datum credential a bench holds, and where it comes from."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.config import BenchConfig
from pilot.config.common import CommonConfig
from pilot.core.bench import Bench
from pilot.core.bench.telemetry import apply_credential
from tests.pilot.commands.test_setup_production import _make_bench


def _enrol_with_central(bench: Bench) -> None:
    from pilot.config.central import CentralConfig
    common = CommonConfig.read(bench.path.parent)
    common.central = CentralConfig(enabled=True, bootstrapped=True)
    common.write(bench.path.parent)
    bench.config = BenchConfig.read(bench.path)


def test_the_credential_fetches_from_central_and_persists(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)
    _enrol_with_central(bench)

    with patch("pilot.integrations.central.CentralClient") as client_cls:
        client_cls.return_value.datum_token.return_value = {
            "token": "jwt-datum",
            "endpoint": "https://datum.region.test",
        }
        apply_credential(bench)

    assert bench.config.telemetry.token == "jwt-datum"
    assert bench.config.telemetry.endpoint == "https://datum.region.test"

    # Persisted, so the next run ships without asking Central again.
    persisted = CommonConfig.read(bench.path.parent).telemetry
    assert (persisted.token, persisted.endpoint) == ("jwt-datum", "https://datum.region.test")


def test_the_credential_replaces_what_is_already_there(tmp_path: Path) -> None:
    """Central is the authority, and the token expires, so a setup run takes what Central
    says now rather than keeping a stale copy."""
    bench = _make_bench(tmp_path)
    _enrol_with_central(bench)
    bench.config.telemetry.token = "already-set"
    bench.config.telemetry.endpoint = "https://datum.configured.test"

    with patch("pilot.integrations.central.CentralClient") as client_cls:
        client_cls.return_value.datum_token.return_value = {
            "token": "jwt-datum",
            "endpoint": "https://datum.region.test",
        }
        apply_credential(bench)

    assert bench.config.telemetry.token == "jwt-datum"
    assert bench.config.telemetry.endpoint == "https://datum.region.test"


def test_the_credential_refetches_a_token_with_no_endpoint(tmp_path: Path) -> None:
    """A token with nowhere to ship is not a working config, so Central is asked again."""
    bench = _make_bench(tmp_path)
    _enrol_with_central(bench)
    bench.config.telemetry.token = "stranded"

    with patch("pilot.integrations.central.CentralClient") as client_cls:
        client_cls.return_value.datum_token.return_value = {
            "token": "jwt-datum",
            "endpoint": "https://datum.region.test",
        }
        apply_credential(bench)

    assert bench.config.telemetry.token == "jwt-datum"
    assert bench.config.telemetry.endpoint == "https://datum.region.test"


def test_the_credential_reports_and_continues_when_central_is_unreachable(tmp_path: Path) -> None:
    from pilot.integrations.central.client import CentralClientError

    bench = _make_bench(tmp_path)
    _enrol_with_central(bench)
    reported: list[str] = []

    with patch("pilot.integrations.central.CentralClient") as client_cls:
        client_cls.return_value.datum_token.side_effect = CentralClientError("Cannot reach Central")
        apply_credential(bench, on_progress=reported.append)

    assert bench.config.telemetry.token == ""
    assert "Cannot reach Central" in reported[0]


def test_the_credential_skips_without_central_enrolment(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)

    with patch("pilot.integrations.central.CentralClient") as client_cls:
        apply_credential(bench)

    client_cls.assert_not_called()


def test_nothing_ships_while_central_names_no_datum(tmp_path: Path) -> None:
    """A region whose Cargo has not reported its Datum yet. The token is kept, because it
    is what Central just said, but nothing is sent without somewhere to send to."""
    bench = _make_bench(tmp_path)
    _enrol_with_central(bench)

    with patch("pilot.integrations.central.CentralClient") as client_cls:
        client_cls.return_value.datum_token.return_value = {"token": "jwt-datum", "endpoint": None}
        apply_credential(bench)

    assert bench.config.telemetry.endpoint == ""
    assert not bench.config.telemetry.is_enabled
