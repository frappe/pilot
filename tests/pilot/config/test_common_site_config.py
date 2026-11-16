from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilot.config.common_site_config import update_common_site_config
from pilot.exceptions import MalformedSiteConfig


def test_update_keeps_keys_it_does_not_touch(tmp_path: Path) -> None:
    (tmp_path / "common_site_config.json").write_text(json.dumps({"mail_server": "smtp.example.com"}))

    with update_common_site_config(tmp_path) as config:
        config["maintenance_mode"] = 1

    saved = json.loads((tmp_path / "common_site_config.json").read_text())
    assert saved == {"mail_server": "smtp.example.com", "maintenance_mode": 1}


def test_malformed_file_is_never_rewritten(tmp_path: Path) -> None:
    """A trailing comma must not cost every custom key on `pilot start`."""
    path = tmp_path / "common_site_config.json"
    damaged = '{"mail_server": "smtp.example.com",}'
    path.write_text(damaged)

    with pytest.raises(MalformedSiteConfig, match="not valid JSON"), update_common_site_config(tmp_path):
        pass

    assert path.read_text() == damaged


@pytest.mark.parametrize("existing_mode", [None, 0o644])
def test_update_leaves_the_file_private(tmp_path: Path, existing_mode: int | None) -> None:
    """The file holds S3 and mail secrets, so a new or a loose file ends at 0600."""
    path = tmp_path / "common_site_config.json"
    if existing_mode is not None:
        path.write_text("{}")
        path.chmod(existing_mode)

    with update_common_site_config(tmp_path) as config:
        config["s3_secret_key"] = "secret"

    assert path.stat().st_mode & 0o777 == 0o600
