import pytest

import pilot
from pilot.internal import password_hash


@pytest.fixture(autouse=True)
def _cheap_password_hashing(monkeypatch):
    """Hash at a token cost across the suite. Production keeps the real iteration
    count; hundreds of bench.toml writes at 600k iterations would add minutes."""
    monkeypatch.setattr(password_hash, "ITERATIONS", 1000)


@pytest.fixture(autouse=True)
def _dev_build(monkeypatch):
    """A release tarball's VERSION file left in a checkout must not change test defaults."""
    monkeypatch.setattr(pilot, "__version__", "dev")
    monkeypatch.setattr(pilot, "is_dev_build", True)


@pytest.fixture(autouse=True)
def _no_host_nginx_lock(monkeypatch):
    """The host-wide nginx lock lives in the installation's benches folder, not a test's."""
    from contextlib import nullcontext

    from pilot.managers.nginx import NginxManager

    monkeypatch.setattr(NginxManager, "config_lock", lambda self: nullcontext())


@pytest.fixture(autouse=True)
def _no_privileged_certificate_reads(monkeypatch):
    """Certificate files are root-only, so reading them shells out to sudo. Tests that need a
    certificate patch these themselves."""
    monkeypatch.setattr("pilot.managers.nginx.NginxManager.has_cert", lambda self, site: False)
    monkeypatch.setattr("pilot.core.bench.certificates.read_certificate", lambda cert_file: (None, []))
