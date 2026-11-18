"""Apps that reach the site database over TCP read db_host and db_port from common_site_config.json."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.pilot.commands.test_commands import make_bench


def _write(tmp_path: Path, db_type: str = "mariadb", existing: bool = False) -> dict:
    bench = make_bench(tmp_path)
    bench.sites_path.mkdir(parents=True)
    bench.config.db_type = db_type
    bench.config.mariadb.port = 3310
    bench.config.mariadb.existing = existing
    bench.write_common_site_config()
    return json.loads((bench.sites_path / "common_site_config.json").read_text())


def test_a_managed_mariadb_gives_its_tcp_address(tmp_path: Path) -> None:
    config = _write(tmp_path)

    assert (config["db_host"], config["db_port"]) == ("127.0.0.1", 3310)


@pytest.mark.parametrize(("db_type", "existing"), [("mariadb", True), ("postgres", False), ("sqlite", False)])
def test_other_databases_keep_their_own_address(tmp_path: Path, db_type: str, existing: bool) -> None:
    config = _write(tmp_path, db_type, existing)

    assert "db_host" not in config
    assert "db_port" not in config
