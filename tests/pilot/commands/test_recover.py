import argparse
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pilot.commands.runtime.recover import RecoverCommand
from pilot.config import SiteConfig
from pilot.core.site import Site
from pilot.internal.cli.command import add_command_arguments, command_from_args


def _bench_config(s3_configured: bool = True) -> SimpleNamespace:
    s3 = (
        SimpleNamespace(
            is_configured=True,
            bucket="my-bucket",
            endpoint_url="https://s3.example.com",
            access_key="k",
            secret_key="s",
            region="us-east-1",
        )
        if s3_configured
        else SimpleNamespace(is_configured=False, bucket="")
    )
    return SimpleNamespace(name="test-bench", s3=s3)


def _parse_recover(argv: list[str], bench) -> RecoverCommand:
    parser = argparse.ArgumentParser()
    add_command_arguments(RecoverCommand, parser)
    return command_from_args(RecoverCommand, parser.parse_args(argv), bench=bench)


def test_recover_command_flags(tmp_path: Path) -> None:
    bench = SimpleNamespace(path=tmp_path, sites_path=tmp_path / "sites", config=_bench_config())
    cmd = _parse_recover(["--site", "mysite.localhost", "-t", "20260927_140002", "--dry-run", "--leave-maintenance"], bench)
    assert cmd.site == "mysite.localhost"
    assert cmd.timestamp == "20260927_140002"
    assert cmd.dry_run is True
    assert cmd.leave_maintenance is True


def test_recover_command_dry_run(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    bench_dir = tmp_path / "test-bench"
    sites_dir = bench_dir / "sites"
    site_dir = sites_dir / "site1.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({}))

    bench = SimpleNamespace(
        path=bench_dir,
        sites_path=sites_dir,
        config=_bench_config(),
    )
    site = Site(SiteConfig(name="site1.localhost", apps=[]), bench)
    bench.sites = lambda: [site]
    bench.site = lambda name: site

    fake_offsite = MagicMock()
    fake_offsite.list_backups.return_value = {
        "20260927_140002": {
            "database": "20260927_140002-site1.localhost-database.sql.gz",
            "files": "20260927_140002-site1.localhost-files.tar",
            "site_config": "20260927_140002-site1.localhost-site_config_backup.json",
        }
    }
    monkeypatch.setattr("pilot.core.site.recovery.OffsiteBackup.from_config", lambda *args, **kwargs: fake_offsite)

    cmd = _parse_recover(["--dry-run"], bench)
    cmd.run()

    out = capsys.readouterr().out
    assert "Inspecting offsite S3 backups" in out
    assert "20260927_140002" in out
    assert "files" in out
    assert "site_config" in out


def test_recover_command_execution(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture) -> None:
    bench_dir = tmp_path / "test-bench"
    sites_dir = bench_dir / "sites"
    site_dir = sites_dir / "site1.localhost"
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({}))

    bench = SimpleNamespace(
        path=bench_dir,
        sites_path=sites_dir,
        config=_bench_config(),
    )
    site = Site(SiteConfig(name="site1.localhost", apps=[]), bench)
    bench.sites = lambda: [site]
    bench.site = lambda name: site

    fake_recovery = MagicMock()
    fake_recovery.recover.return_value = "20260927_140002"
    monkeypatch.setattr("pilot.core.site.recovery.SiteRecovery.recover", fake_recovery.recover)

    cmd = _parse_recover(["--site", "site1.localhost"], bench)
    cmd.run()

    out = capsys.readouterr().out
    assert "Successfully recovered site 'site1.localhost'" in out
    assert "Recovery completed successfully" in out
