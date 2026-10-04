from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pilot.exceptions import CommandError
from pilot.managers import sudoers


def _install(monkeypatch: pytest.MonkeyPatch, rejects_wildcards: bool, bare: list[str] | None) -> list[str]:
    written: list[str] = []

    def stage_and_copy(stage_dir, content, target, validate=None):
        if rejects_wildcards and "*" in content:
            raise CommandError("visudo: wildcards are not allowed in command arguments", returncode=1)
        written.append(content)

    monkeypatch.setattr(sudoers, "stage_and_copy", stage_and_copy)
    monkeypatch.setattr(sudoers, "run_command", lambda argv: None)
    monkeypatch.setattr(sudoers, "is_sudo_rs", lambda: rejects_wildcards)
    sudoers.install_sudoers_grant(Path("/tmp"), "frappe", "certbot", ["/usr/bin/test -f /a/*/b"], bare)
    return written


def test_classic_sudo_keeps_the_argument_limits(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _install(monkeypatch, rejects_wildcards=False, bare=["/usr/bin/test"]) == [
        "frappe ALL=(ALL) NOPASSWD: /usr/bin/test -f /a/*/b\n"
    ]


def test_sudo_rs_falls_back_to_the_bare_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _install(monkeypatch, rejects_wildcards=True, bare=["/usr/bin/test"]) == [
        "frappe ALL=(ALL) NOPASSWD: /usr/bin/test\n"
    ]


def test_a_rejected_grant_without_a_fallback_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    with pytest.raises(CommandError):
        _install(monkeypatch, rejects_wildcards=True, bare=None)


def test_classic_sudo_never_falls_back_to_bare_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    """Only sudo-rs rejects wildcards; any other visudo failure is a real error."""
    monkeypatch.setattr(sudoers, "stage_and_copy", MagicMock(side_effect=CommandError("syntax error", returncode=1)))
    monkeypatch.setattr(sudoers, "is_sudo_rs", lambda: False)

    with pytest.raises(CommandError):
        sudoers.install_sudoers_grant(Path("/tmp"), "frappe", "certbot", ["/usr/bin/test -f /a/*/b"], ["/usr/bin/test"])
