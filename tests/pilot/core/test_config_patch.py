from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilot.config.bench import BenchConfig
from pilot.config.common import CommonConfig
from pilot.config.common_site_config import update_common_site_config
from pilot.core.bench.config_patch import ConfigPatch
from pilot.exceptions import ConfigError
from pilot.internal.json_merge_patch import apply_merge_patch
from tests.pilot.integrations.test_central_client import _bench


def test_merge_patch_follows_rfc_7396() -> None:
    target = {"s3": {"bucket": "a", "region": "x"}, "tags": [1, 2], "old": True, "scalar": 1}

    apply_merge_patch(target, {"s3": {"bucket": "b"}, "tags": [3], "old": None, "scalar": {"nested": None, "k": 1}})

    assert target == {"s3": {"bucket": "b", "region": "x"}, "tags": [3], "scalar": {"k": 1}}


def test_a_patch_keeps_settings_it_does_not_name(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    with update_common_site_config(bench.sites_path) as config:
        config["mail_server"] = "smtp.example.test"

    patch = ConfigPatch(
        common_config={"proxy": {"protocol_v2": True}},
        bench_config={"gunicorn": {"workers": 6}},
        common_site_config={"max_file_size": 1024},
    )
    patch.apply(bench)
    patch.apply(bench)

    saved = BenchConfig.read(bench.path)
    assert saved.proxy.protocol_v2 is True
    assert saved.gunicorn.workers == 6
    assert saved.mariadb.root_password == "root"
    site_config = json.loads((bench.sites_path / "common_site_config.json").read_text())
    assert site_config["mail_server"] == "smtp.example.test"
    assert site_config["max_file_size"] == 1024


@pytest.mark.parametrize(
    ("patch", "field"),
    [
        (ConfigPatch(bench_config={"gunicorn": {"wokers": 6}}), "gunicorn.wokers"),
        (ConfigPatch(common_config={"telemetry": {"tokn": "x"}}), "telemetry.tokn"),
    ],
)
def test_an_unknown_toml_key_is_rejected_before_writing(tmp_path: Path, patch: ConfigPatch, field: str) -> None:
    bench = _bench(tmp_path)
    before = CommonConfig.path(bench.path.parent).read_text()

    with pytest.raises(ConfigError, match=field):
        patch.apply(bench)

    assert CommonConfig.path(bench.path.parent).read_text() == before
