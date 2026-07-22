"""Tests for SiteAccessLogProvider's parsing of the nginx access log."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

from admin.backend.providers.site_access_log import SiteAccessLogProvider

_TIME_FORMAT = "%d/%b/%Y:%H:%M:%S %z"


def _line(ip: str, host: str, when: datetime, path: str = "/desk", request_time: str = "0.059") -> str:
    return f'{ip} [{when.strftime(_TIME_FORMAT)}] "GET {path}" 200 "{host}" {request_time}'


def _write_log(tmp_path: Path, lines: list[str]) -> Path:
    logs = tmp_path / "logs"
    logs.mkdir(exist_ok=True)
    (logs / "nginx-access.log").write_text("\n".join(lines) + "\n")
    return tmp_path


def test_parses_ip_and_host(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = _write_log(tmp_path, [_line("10.27.0.2", "some-site.frappe.cloud", now)])

    result = SiteAccessLogProvider(root, "some-site.frappe.cloud", "1h").get_top_ips()

    assert result["categories"] == ["10.27.0.2"]


def test_filters_by_host(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = _write_log(
        tmp_path,
        [
            _line("1.1.1.1", "site-a.local", now),
            _line("2.2.2.2", "site-b.local", now),
            _line("1.1.1.1", "site-a.local", now, path="/api/method/x"),
        ],
    )

    site_a = SiteAccessLogProvider(root, "site-a.local", "1h").get_top_ips()
    site_b = SiteAccessLogProvider(root, "site-b.local", "1h").get_top_ips()

    assert site_a["categories"] == ["1.1.1.1"]
    assert site_a["points"][0]["1.1.1.1"] == 2
    assert site_b["categories"] == ["2.2.2.2"]


def test_respects_window_cutoff(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    old = now - timedelta(hours=5)
    root = _write_log(
        tmp_path,
        [
            _line("9.9.9.9", "site-a.local", old),
            _line("1.1.1.1", "site-a.local", now),
        ],
    )

    result = SiteAccessLogProvider(root, "site-a.local", "1h").get_top_ips()

    assert result["categories"] == ["1.1.1.1"]


def test_old_line_for_different_host_still_stops_the_scan(tmp_path: Path) -> None:
    # The file is chronologically ordered across every site's traffic, so an
    # old line for a different host must still end the scan - otherwise a
    # busy neighbor site could mask stale entries for the site being queried.
    now = datetime.now(UTC)
    very_old = now - timedelta(hours=10)
    old = now - timedelta(hours=5)
    root = _write_log(
        tmp_path,
        [
            _line("9.9.9.9", "site-a.local", very_old),
            _line("8.8.8.8", "site-b.local", old),
        ],
    )

    result = SiteAccessLogProvider(root, "site-a.local", "1h").get_top_ips()

    assert result["categories"] == []


def test_malformed_line_skipped(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = _write_log(
        tmp_path,
        [
            "this is not a valid access log line",
            _line("1.1.1.1", "site-a.local", now),
        ],
    )

    result = SiteAccessLogProvider(root, "site-a.local", "1h").get_top_ips()

    assert result["categories"] == ["1.1.1.1"]


def test_dash_request_time_does_not_raise(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    root = _write_log(tmp_path, [_line("1.1.1.1", "site-a.local", now, request_time="-")])

    result = SiteAccessLogProvider(root, "site-a.local", "1h").get_top_ips()

    assert result["categories"] == ["1.1.1.1"]


def test_is_available_false_when_missing(tmp_path: Path) -> None:
    provider = SiteAccessLogProvider(tmp_path, "site-a.local", "1h")

    assert provider.is_available() is False


def test_is_available_true_when_present(tmp_path: Path) -> None:
    root = _write_log(tmp_path, [_line("1.1.1.1", "site-a.local", datetime.now(UTC))])

    assert SiteAccessLogProvider(root, "site-a.local", "1h").is_available() is True
