import json
import stat

import pytest

from pilot.config import BackupConfig
from pilot.config.site_backup import clear_retention, read_retention, write_retention


def test_write_creates_and_read_roundtrips(tmp_path) -> None:
    path = tmp_path / "site_config.json"
    write_retention(path, BackupConfig(scheme="fifo", keep_last=3))

    config = read_retention(path)
    assert config.scheme == "fifo"
    assert config.keep_last == 3
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_write_preserves_other_keys(tmp_path) -> None:
    path = tmp_path / "site_config.json"
    path.write_text(json.dumps({"db_name": "x", "db_password": "secret"}))

    write_retention(path, BackupConfig())
    data = json.loads(path.read_text())
    assert data["db_password"] == "secret"
    assert data["backup_retention"]["scheme"] == "gfs"


def test_corrupt_config_is_not_clobbered_on_write(tmp_path) -> None:
    path = tmp_path / "site_config.json"
    path.write_text("{ not valid json")

    with pytest.raises(json.JSONDecodeError):
        write_retention(path, BackupConfig())
    assert path.read_text() == "{ not valid json"  # left intact, not erased


def test_read_returns_none_on_missing_or_corrupt(tmp_path) -> None:
    assert read_retention(tmp_path / "missing.json") is None

    corrupt = tmp_path / "site_config.json"
    corrupt.write_text("{ not valid json")
    assert read_retention(corrupt) is None


def test_clear_removes_only_retention(tmp_path) -> None:
    path = tmp_path / "site_config.json"
    path.write_text(json.dumps({"db_name": "x", "backup_retention": {"scheme": "gfs"}}))

    clear_retention(path)
    data = json.loads(path.read_text())
    assert "backup_retention" not in data
    assert data["db_name"] == "x"
