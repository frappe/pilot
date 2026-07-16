"""Tests for pilot.tasks.jobs.marketplace_fetcher.MarketplaceFetcher.

Shared by GetAppTask, GetAndInstallAppTask, and NewSiteTask so each doesn't
duplicate marketplace dependency resolution.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from pilot.tasks.jobs.marketplace_fetcher import MarketplaceFetcher
from pilot.core.marketplace import Marketplace, Resolver
from pilot.exceptions import BenchError


def resolver(name: str, deps: dict[str, str] | None = None) -> Resolver:
    return Resolver(
        app=name,
        repo=f"https://github.com/frappe/{name}",
        target_type="branch",
        target="main",
        version="1.0.0",
        frappe_version="16.0.0",
        required_version="",
        is_installable=True,
        dependencies=deps or {},
    )


def test_fetch_raises_when_app_not_in_marketplace() -> None:
    fetcher = MarketplaceFetcher(MagicMock(), step=MagicMock())

    with patch.object(Marketplace, "read_all_apps", return_value=[]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"):
        with pytest.raises(BenchError, match="not found in marketplace"):
            fetcher.fetch("unknown_app")


def test_fetch_reports_step_and_runs_get_app() -> None:
    helpdesk = resolver("helpdesk")
    step = MagicMock()
    fetcher = MarketplaceFetcher(MagicMock(), step=step)

    with patch.object(Marketplace, "read_all_apps", return_value=[helpdesk]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch("pilot.commands.get_app.GetAppCommand.run") as mock_run:
        cmds = fetcher.fetch("helpdesk")

    step.assert_called_once_with("fetch", "Fetch helpdesk")
    mock_run.assert_called_once()
    assert [c.name for c in cmds] == ["helpdesk"]


def test_fetch_resolves_dependencies_before_the_app_itself() -> None:
    dep = resolver("frappe_payments_dep")
    top = resolver("payments", deps={"frappe_payments_dep": ""})
    top._registry = {"frappe_payments_dep": [dep]}

    fetched: list[str] = []

    def fake_run(self):
        fetched.append(self.name)

    fetcher = MarketplaceFetcher(MagicMock(), step=MagicMock())

    with patch.object(Marketplace, "read_all_apps", return_value=[top]), \
            patch.object(Marketplace, "get_current_frappe_version", return_value="16.0.0"), \
            patch("pilot.commands.get_app.GetAppCommand.run", fake_run):
        fetcher.fetch("payments")

    assert fetched == ["frappe_payments_dep", "payments"]
