"""Tests for WafManager CRS install integrity (pinned SHA-256 verification)."""

from __future__ import annotations

import hashlib
import io
import tarfile
from pathlib import Path

import pytest

from pilot.managers import waf
from pilot.managers.waf import WafManager


def _crs_tarball() -> bytes:
    """A minimal CRS-shaped archive: coreruleset-<version>/{crs-setup.conf.example, rules/}."""
    buffer = io.BytesIO()
    top = f"coreruleset-{waf.CRS_VERSION}"
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        setup = tarfile.TarInfo(f"{top}/crs-setup.conf.example")
        setup.size = 0
        tar.addfile(setup, io.BytesIO(b""))
        rule = tarfile.TarInfo(f"{top}/rules/REQUEST-942.conf")
        body = b"# rule\n"
        rule.size = len(body)
        tar.addfile(rule, io.BytesIO(body))
    return buffer.getvalue()


@pytest.fixture
def fake_download(monkeypatch, tmp_path):
    """Make urlretrieve write chosen bytes, and land the CRS under a temp root."""
    import pilot.utils

    monkeypatch.setattr(pilot.utils, "cli_root", lambda: tmp_path)

    def _install(payload: bytes, expected_sha: str | None = None):
        if expected_sha is not None:
            monkeypatch.setattr(waf, "_CRS_SHA256", expected_sha)

        def _urlretrieve(url, dest):
            Path(dest).write_bytes(payload)

        monkeypatch.setattr(waf.urllib.request, "urlretrieve", _urlretrieve)
        return tmp_path / "modsecurity-crs"

    return _install


def test_install_crs_rejects_tampered_archive(fake_download) -> None:
    shared = fake_download(b"not the real crs")  # hash won't match the pin
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        WafManager()._install_crs()
    assert not shared.exists()  # nothing extracted, nothing installed


def test_install_crs_accepts_matching_archive(fake_download) -> None:
    payload = _crs_tarball()
    shared = fake_download(payload, expected_sha=hashlib.sha256(payload).hexdigest())

    WafManager()._install_crs()

    assert (shared / "crs-setup.conf").exists()
    assert (shared / "rules" / "REQUEST-942.conf").exists()


def test_pinned_digest_is_a_sha256() -> None:
    assert len(waf._CRS_SHA256) == 64
    int(waf._CRS_SHA256, 16)  # hex
