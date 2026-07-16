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
def fake_download(monkeypatch):
    """Make urlretrieve write chosen bytes, and record any privileged commands so a
    test can assert none ran."""
    ran: list = []
    monkeypatch.setattr(waf, "run_command", lambda cmd: ran.append(cmd))

    def _install(payload: bytes, expected_sha: str | None = None):
        if expected_sha is not None:
            monkeypatch.setattr(waf, "_CRS_SHA256", expected_sha)

        def _urlretrieve(url, dest):
            Path(dest).write_bytes(payload)
        monkeypatch.setattr(waf.urllib.request, "urlretrieve", _urlretrieve)
        return ran

    return _install


def test_install_crs_rejects_tampered_archive(fake_download) -> None:
    ran = fake_download(b"not the real crs")  # hash won't match the pin
    with pytest.raises(RuntimeError, match="checksum mismatch"):
        WafManager()._install_crs()
    assert ran == []  # nothing extracted, nothing copied under /usr/share


def test_install_crs_accepts_matching_archive(fake_download) -> None:
    payload = _crs_tarball()
    ran = fake_download(payload, expected_sha=hashlib.sha256(payload).hexdigest())

    WafManager()._install_crs()

    # crs-setup.conf + rules/ copied into the shared dir via privileged commands.
    assert any("crs-setup.conf" in " ".join(map(str, cmd)) for cmd in ran)
    assert any(str(waf.SHARED_MODSEC_DIR / "rules") in " ".join(map(str, cmd)) for cmd in ran)


def test_pinned_digest_is_a_sha256() -> None:
    assert len(waf._CRS_SHA256) == 64
    int(waf._CRS_SHA256, 16)  # hex
