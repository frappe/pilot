import json
from unittest.mock import patch

import pytest

from pilot.core.bench import Bench
from pilot.internal.patch_state import is_applied
from pilot.managers.nginx import NginxManager
from pilot.patches.serve_site_embeds_locally import PATCH_NAME, run


@pytest.fixture
def bench(tmp_path):
    path = tmp_path / "main"
    path.mkdir()
    (path / "bench.toml").write_text(
        '[bench]\nname = "main"\npython = "3.14"\n'
        '[production]\nenabled = true\nprocess_manager = "systemd"\n[admin]\ndomain = "admin.local"\n'
    )
    sites = path / "sites"
    sites.mkdir()
    (sites / "common_site_config.json").write_text('{"pilot_endpoint": "http://127.0.0.1:8001"}')
    for name, config in {
        "managed": {
            "cloud_settings_embed_url": "http://old-admin.local",
            "cloud_server_url": "http://admin.local",
        },
        "unmanaged": {"db_name": "unchanged"},
    }.items():
        (sites / name).mkdir()
        (sites / name / "site_config.json").write_text(json.dumps(config))
    (sites / "alias").symlink_to(sites / "managed", target_is_directory=True)
    return Bench(path)


def test_migration_publishes_before_writing_and_runs_once(bench):
    common = bench.sites_path / "common_site_config.json"
    events = []

    def publish(self):
        assert "cloud_settings_embed_url" not in json.loads(common.read_text())
        events.append("nginx")

    def clear(self):
        assert json.loads(common.read_text())["cloud_settings_embed_url"] == "/_pilot"
        events.append("cache")

    with patch.object(NginxManager, "apply_config", publish), patch.object(Bench, "clear_cache", clear):
        run(bench.path.parent)
        run(bench.path.parent)

    assert events == ["nginx", "cache"]
    assert json.loads((bench.sites_path / "managed/site_config.json").read_text()) == {
        "cloud_settings_embed_url": "/_pilot",
        "cloud_server_url": "http://admin.local",
    }
    assert json.loads((bench.sites_path / "unmanaged/site_config.json").read_text()) == {
        "db_name": "unchanged"
    }


@pytest.mark.parametrize("failure", ["nginx", "cache"])
def test_failed_migration_can_retry(bench, failure):
    with (
        patch.object(
            NginxManager, "apply_config", side_effect=RuntimeError("failed") if failure == "nginx" else None
        ),
        patch.object(
            Bench, "clear_cache", side_effect=RuntimeError("failed") if failure == "cache" else None
        ),
        pytest.raises(RuntimeError, match="failed"),
    ):
        run(bench.path.parent)
    assert not is_applied(bench.path, PATCH_NAME)
    if failure == "nginx":
        assert "cloud_settings_embed_url" not in json.loads(
            (bench.sites_path / "common_site_config.json").read_text()
        )

    with patch.object(NginxManager, "apply_config"), patch.object(Bench, "clear_cache") as clear:
        run(bench.path.parent)
    clear.assert_called_once()
    assert is_applied(bench.path, PATCH_NAME)


def test_development_bench_keeps_existing_urls(bench):
    bench.config.production.enabled = False
    bench.config.write(bench.path)
    with patch.object(NginxManager, "apply_config") as publish, patch.object(Bench, "clear_cache") as clear:
        run(bench.path.parent)
    publish.assert_not_called()
    clear.assert_not_called()
    assert (
        json.loads((bench.sites_path / "managed/site_config.json").read_text())["cloud_settings_embed_url"]
        == "http://old-admin.local"
    )
