from __future__ import annotations

from pathlib import Path

import pytest

from pilot.config import SiteConfig
from pilot.core.site import Site
from tests.pilot.commands.test_commands import make_bench


def test_restore_recreates_runtime_directories_and_keeps_existing_logs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bench = make_bench(tmp_path)
    bench.config.db_type = "sqlite"
    site = Site(SiteConfig(name="restored.localhost", apps=[]), bench)
    site.path.mkdir(parents=True)
    config = '{"db_type": "sqlite", "db_name": "restored"}'
    (site.path / "site_config.json").write_text(config)

    def restore_command(cmd: list[str], **kwargs) -> None:
        assert cmd[cmd.index("restore") + 1] == "backup.sql.gz"
        with (site.path / "logs" / "database.log").open("a") as log:
            log.write("restored\n")
        (site.path / "locks" / "restore.lock").touch()

    monkeypatch.setattr("pilot.core.site.commands.run_command", restore_command)
    site.restore("backup.sql.gz")
    site.restore("backup.sql.gz")

    assert (site.path / "logs" / "database.log").read_text() == "restored\nrestored\n"
    assert (site.path / "locks" / "restore.lock").is_file()
    assert (site.path / "site_config.json").read_text() == config
