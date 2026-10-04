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
