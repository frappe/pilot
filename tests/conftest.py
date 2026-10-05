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
