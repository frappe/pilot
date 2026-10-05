from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.site.frappe_cloud import PASS_CODE_ALPHABET, SiteFrappeCloud, make_pass_code
from pilot.exceptions import FrappeCloudError


def _frappe_cloud(tmp_path: Path) -> SiteFrappeCloud:
    site = MagicMock()
    site.config.name = "a.localhost"
    site.bench.config_path = tmp_path / "config"
    site.bench.config.frappe_cloud.url = "https://cloud.example.com"
    return SiteFrappeCloud(site)


def test_a_pass_code_has_eight_unambiguous_characters_and_never_only_digits() -> None:
    codes = [make_pass_code() for _ in range(500)]

    assert all(len(code) == 8 and set(code) <= set(PASS_CODE_ALPHABET) for code in codes)
    assert not any(code.isdigit() for code in codes)


def test_a_pass_code_made_of_digits_is_drawn_again() -> None:
    draws = iter("23456789" + "A2345678")
    with patch("pilot.core.site.frappe_cloud.secrets.choice", side_effect=lambda _: next(draws)):
        assert make_pass_code() == "A2345678"


def test_connecting_keeps_the_token_in_a_private_file_and_sends_the_code(tmp_path: Path) -> None:
    frappe_cloud = _frappe_cloud(tmp_path)
    response = {
        "token": "secret-token",
        "site": "old.frappe.cloud",
        "approval_url": "https://cloud.example.com/x",
        "expires_at": "2026-10-04T12:10:00+05:30",
    }

    with patch("pilot.core.site.frappe_cloud.FrappeCloud.request_access", return_value=response) as request_access:
        connection = frappe_cloud.connect("erp.example.com")

    request_access.assert_called_once_with("erp.example.com", connection["code"])
    assert json.loads(frappe_cloud.path.read_text())["token"] == "secret-token"
    assert stat.S_IMODE(frappe_cloud.path.stat().st_mode) == 0o600


def test_a_failed_revoke_still_removes_the_local_token(tmp_path: Path) -> None:
    frappe_cloud = _frappe_cloud(tmp_path)
    frappe_cloud.path.parent.mkdir(parents=True)
    frappe_cloud.path.write_text(json.dumps({"url": "https://cloud.example.com", "token": "t"}))

    with patch("pilot.core.site.frappe_cloud.FrappeCloud.revoke", side_effect=FrappeCloudError("down")) as revoke:
        frappe_cloud.disconnect()

    revoke.assert_called_once()
    assert not frappe_cloud.path.exists()


def test_backups_need_a_connection_first(tmp_path: Path) -> None:
    with pytest.raises(FrappeCloudError, match="Connect to Frappe Cloud first"):
        _ = _frappe_cloud(tmp_path).client


def test_a_restore_revokes_its_own_token_and_keeps_a_connection_made_since(tmp_path: Path) -> None:
    frappe_cloud = _frappe_cloud(tmp_path)
    frappe_cloud.path.parent.mkdir(parents=True)
    frappe_cloud.path.write_text(json.dumps({"url": "https://cloud.example.com", "token": "new-token"}))

    with patch("pilot.core.site.frappe_cloud.FrappeCloud") as client:
        frappe_cloud.disconnect("old-token")

    client.assert_called_once_with("https://cloud.example.com", "old-token")
    assert json.loads(frappe_cloud.path.read_text())["token"] == "new-token"
