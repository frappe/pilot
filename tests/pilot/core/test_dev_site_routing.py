from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.bench.runtime import BenchRuntime
from tests.pilot.commands.test_commands import make_bench


@pytest.mark.parametrize(
    ("site_count", "default_site", "env_site", "warn"),
    [
        (2, "one.localhost", "", True),
        (2, "", "one.localhost", True),
        (1, "one.localhost", "", False),
        (2, "", "", False),
    ],
)
def test_dev_start_warns_when_a_selector_pins_a_multi_site_bench(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    site_count: int,
    default_site: str,
    env_site: str,
    warn: bool,
) -> None:
    bench = make_bench(tmp_path)
    (bench.env_path / "bin").mkdir(parents=True)
    bench.python.touch()
    for name in ("one.localhost", "two.localhost")[:site_count]:
        site = bench.sites_path / name
        site.mkdir(parents=True)
        (site / "site_config.json").write_text("{}")
    config_path = bench.sites_path / "common_site_config.json"
    config = json.dumps({"default_site": default_site})
    config_path.write_text(config)
    monkeypatch.setenv("FRAPPE_SITE", env_site)
    messages: list[str] = []

    with (
        patch("pilot.managers.processes.local.ProcessManager.stop"),
        patch("pilot.managers.processes.local.ProcessManager.start") as start,
        patch.object(BenchRuntime, "_rebuild_manager_config"),
        patch.object(BenchRuntime, "_ensure_admin_dist"),
    ):
        BenchRuntime(bench).start(messages.append)

    warnings = [message for message in messages if "Host-based routing" in message]
    assert bool(warnings) is warn
    if warn:
        assert "one.localhost" in warnings[0]
    assert config_path.read_text() == config
    start.assert_called_once()
