from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilot.config import LiteModeConfig
from pilot.core.bench.config_files import MANAGED_COMMON_SITE_CONFIG_KEYS
from pilot.exceptions import BenchError
from tests.pilot.managers.test_lite_mode import make_bench


@pytest.mark.parametrize("lite", [True, False])
def test_every_key_pilot_writes_is_managed(tmp_path: Path, lite: bool) -> None:
    bench = make_bench(tmp_path, LiteModeConfig(enabled=lite))
    bench.sites_path.mkdir(parents=True, exist_ok=True)
    bench.write_common_site_config()

    written = json.loads((bench.sites_path / "common_site_config.json").read_text())

    assert set(written) <= MANAGED_COMMON_SITE_CONFIG_KEYS
    assert bench.public_common_site_config == {}


def test_nested_secrets_cannot_be_written(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.sites_path.mkdir(parents=True, exist_ok=True)

    with pytest.raises(BenchError):
        bench.update_public_common_site_config({"provider": {"client_secret": "x"}})

    assert bench.update_public_common_site_config({"provider": {"region": "eu"}}) == {
        "provider": {"region": "eu"}
    }
