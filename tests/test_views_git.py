"""Tests for the admin Git integration view helpers."""
from __future__ import annotations

from admin.backend.views.git import _mask_token, _status


def test_mask_token_keeps_short_tokens_unmasked() -> None:
    assert _mask_token("short") == "short"


def test_mask_token_masks_middle_of_long_tokens() -> None:
    masked = _mask_token("ghp_abcdefghijklmnopqrstuvwxyz")
    assert masked == "ghp_xxxxxxxxwxyz"
    assert "abcdefghijklmnop" not in masked


def test_status_includes_token_preview_when_connected() -> None:
    record = {"provider": "github", "username": "octocat", "token": "ghp_abcdefghijklmnopqrstuvwxyz"}
    status = _status(record)
    assert status["connected"] is True
    assert status["token_preview"] == _mask_token(record["token"])
    assert "token" not in status


def test_status_disconnected_has_no_token_preview() -> None:
    assert _status(None) == {"connected": False, "providers": _status(None)["providers"]}
