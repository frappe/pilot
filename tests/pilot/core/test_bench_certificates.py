from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.bench.certificates import CertificateFile, CertificateRecord
from pilot.exceptions import CommandError
from pilot.managers.letsencrypt import LetsEncryptManager
from tests.pilot.integrations.test_central_client import _bench
from tests.pilot.managers.test_letsencrypt_per_domain import _site

SITE = "site.example.com"


def _in(days: float) -> str:
    return (datetime.now(UTC) + timedelta(days=days)).isoformat()


COVERED = {"domains": [SITE], "covered": [SITE]}


@pytest.mark.parametrize(
    ("record", "state"),
    [
        (CertificateRecord(SITE, [SITE]), "pending"),
        (CertificateRecord(SITE, **COVERED, expires_at=_in(60.5)), "valid"),
        (CertificateRecord(SITE, **COVERED, expires_at=_in(9.5)), "expiring"),
        (CertificateRecord(SITE, **COVERED, expires_at=_in(-1)), "expired"),
        (CertificateRecord(SITE, [SITE], error="rate limited"), "failed"),
        (CertificateRecord(SITE, **COVERED, expires_at=_in(25.5), error="rate limited"), "failed"),
        (CertificateRecord(SITE, **COVERED, expires_at=_in(-1), error="rate limited"), "expired"),
    ],
)
def test_the_state_comes_from_the_expiry_and_the_last_attempt(record: CertificateRecord, state: str) -> None:
    assert record.state == state
    assert record.is_broken == (state in {"failed", "expiring", "expired"})


def test_a_new_domain_that_failed_does_not_break_the_names_the_file_still_covers() -> None:
    record = CertificateRecord(SITE, [SITE, "shop.example.com"], [SITE], expires_at=_in(80.5), error="timeout")

    assert record.get_domain_state(SITE) == "valid"
    assert record.get_domain_state("shop.example.com") == "failed"
    assert record.state == "failed"
    assert record.get_alert_payload("bench", SITE)["title"] == "Certificate for shop.example.com failed to issue"


def test_a_new_domain_waits_for_its_certificate() -> None:
    record = CertificateRecord(SITE, [SITE, "shop.example.com"], [SITE], expires_at=_in(80.5))

    assert record.get_domain_state("shop.example.com") == "pending"


def test_a_success_clears_the_last_failure(tmp_path: Path) -> None:
    certificates = _bench(tmp_path).certificates
    certificates.record_failure(SITE, [SITE], "Certbot failed: rate limited\n")

    with patch("pilot.core.bench.certificates.read_certificate", return_value=(_in(89.5), [SITE])):
        certificates.record_success(SITE, [SITE], tmp_path / "fullchain.pem")

    record = certificates.get(SITE)
    assert record.state == "valid" and record.error is None and record.failed_at is None


def _alert(certificates, files):
    with (
        patch("pilot.core.notification.events.record_alert"),
        patch("pilot.core.notification.notifier.Notifier.send") as notify,
    ):
        certificates.alert_broken(files)
    return [call.args[0] for call in notify.call_args_list]


def test_a_broken_certificate_alerts_once_until_it_is_valid_again(tmp_path: Path) -> None:
    certificates = _bench(tmp_path).certificates
    files = [CertificateFile(SITE, [SITE], tmp_path / "fullchain.pem", SITE)]
    certificates.record_failure(SITE, [SITE], "rate limited")

    first = _alert(certificates, files)
    again = _alert(certificates, files)
    with patch("pilot.core.bench.certificates.read_certificate", return_value=(_in(20.5), [SITE])):
        certificates.record_success(SITE, [SITE], files[0].path)
    recovered = _alert(certificates, files)
    certificates.record_failure(SITE, [SITE], "rate limited")
    broken_again = _alert(certificates, files)

    assert [payload["event"] for payload in first] == ["certificate_failed"]
    assert first[0]["title"] == f"Certificate for {SITE} failed to issue"
    assert first[0]["site"] == SITE and first[0]["severity"] == "Error"
    assert again == [] and recovered == []
    assert broken_again[0]["title"] == f"Certificate for {SITE} failed to renew"


def test_a_failed_issue_is_recorded_and_alerted(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, SITE, ssl=True)

    with (
        patch.object(LetsEncryptManager, "obtain", side_effect=CommandError("too many certificates")),
        patch("pilot.core.notification.events.record_alert"),
        patch("pilot.core.notification.notifier.Notifier.send") as notify,
    ):
        LetsEncryptManager(bench).obtain_all()

    record = bench.certificates.get(SITE)
    assert record.state == "failed" and "too many certificates" in record.error
    assert notify.call_args.args[0]["event"] == "certificate_failed"


def test_a_domain_shows_the_tls_state_of_its_certificate(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, SITE, ssl=True)
    domains = bench.site(SITE).domains
    config = domains._site_config()

    assert domains.get_tls_status(config, SITE)["state"] == "pending"

    bench.certificates.record_failure(SITE, [SITE], "DNS problem: NXDOMAIN")

    status = domains.get_tls_status(config, SITE)
    assert status["state"] == "failed"
    assert status["error"] == "DNS problem: NXDOMAIN"


def test_a_domain_without_local_tls_has_no_tls_state(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, "site.localhost")
    domains = bench.site("site.localhost").domains

    assert domains.get_tls_status(domains._site_config(), "site.localhost") is None
