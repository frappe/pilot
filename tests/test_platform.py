"""Tests for pilot.platform helpers."""
from __future__ import annotations

from pathlib import Path

from pilot import platform


def test_which_searches_sbin_when_path_is_minimal(tmp_path: Path, monkeypatch) -> None:
    sbin = tmp_path / "usr" / "sbin"
    sbin.mkdir(parents=True)
    daemon = sbin / "mariadbd"
    daemon.write_text("#!/bin/sh\n")
    daemon.chmod(0o755)

    # Minimal PATH without the sbin dir — shutil.which alone would miss it.
    monkeypatch.setenv("PATH", str(tmp_path / "bin"))
    monkeypatch.setattr(platform, "_EXTRA_BIN_DIRS", (str(sbin),))

    assert platform.which("mariadbd") == str(daemon)


def test_which_returns_none_for_missing(monkeypatch) -> None:
    monkeypatch.setattr(platform, "_EXTRA_BIN_DIRS", ())
    assert platform.which("definitely-not-a-real-binary-xyz") is None
