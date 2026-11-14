from unittest import mock

import pytest


@pytest.fixture(autouse=True)
def _stop_marketplace_stubs():
    """Undo patchers left running by tests/pilot/marketplace_registry.publish."""
    yield
    mock.patch.stopall()


@pytest.fixture
def legacy_path(tmp_path):
    """A bench made by the `bench` CLI: frappe, erpnext, an unused app and one site."""
    from tests.pilot.legacy_bench import make_legacy_bench

    return make_legacy_bench(tmp_path)
