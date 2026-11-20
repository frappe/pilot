from __future__ import annotations

from unittest.mock import MagicMock, patch

from pilot.core.database.base import QueryResult
from pilot.core.site.history import SiteHistoryReader, build_history


def test_build_history_falls_back_to_creation_for_unlogged_installs() -> None:
    rows = [
        ["frappe", "16.2.1", "version-16", "2026-11-18 12:14:59"],
        ["wiki", "3.3.0", "develop", "2026-11-18 12:15:00"],
    ]

    history = build_history("2026-10-06 22:00:38", rows, {"wiki": "2026-11-18T06:45:26+00:00"})

    assert history.frappe_version == "16.2.1"
    assert history.frappe_branch == "version-16"
    assert history.updated_at == "2026-11-18 12:15:00"
    assert [(app.app, app.installed_at) for app in history.apps] == [
        ("frappe", "2026-10-06 22:00:38"),
        ("wiki", "2026-11-18T06:45:26+00:00"),
    ]


def test_install_times_keep_the_latest_install_per_app() -> None:
    site = MagicMock()
    site.config.name = "s.localhost"
    entries = [
        {"type": "app", "event": "installed", "app": "wiki", "logged_at": "2026-11-18T06:00:00+00:00"},
        {"type": "app", "event": "uninstalled", "app": "crm", "logged_at": "2026-11-10T06:00:00+00:00"},
        {"type": "app", "event": "installed", "app": "wiki", "logged_at": "2026-11-01T06:00:00+00:00"},
    ]

    with patch("pilot.core.bench.audit_log.AuditLog") as audit:
        audit.return_value.entries.return_value = entries
        times = SiteHistoryReader(site).get_install_times()

    assert times == {"wiki": "2026-11-18T06:00:00+00:00"}


def test_get_reads_creation_and_installed_apps_from_the_site_database() -> None:
    site = MagicMock()
    database = MagicMock()
    database.quote_identifier.side_effect = lambda name: f"`{name}`"
    database.execute.side_effect = [
        QueryResult(columns=[], rows=[["2026-10-06 22:00:38"]], duration_ms=1),
        QueryResult(columns=[], rows=[["frappe", "16.2.1", "version-16", "2026-11-18 12:14:59"]], duration_ms=1),
        QueryResult(columns=[], rows=[[4]], duration_ms=1),
    ]

    with (
        patch("pilot.core.database.make_site_database", return_value=database),
        patch.object(SiteHistoryReader, "get_install_times", return_value={}),
    ):
        history = SiteHistoryReader(site).get()

    assert history.created_at == "2026-10-06 22:00:38"
    assert [app.app for app in history.apps] == ["frappe"]
    assert history.users == 4
