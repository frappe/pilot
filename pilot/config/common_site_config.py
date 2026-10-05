from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pilot.exceptions import MalformedSiteConfig
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked


def read_common_site_config(sites_path: Path) -> dict:
    """A missing file reads as empty. Damaged JSON raises, so a merge-and-write cannot drop its settings."""
    path = sites_path / "common_site_config.json"
    try:
        config = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except ValueError as error:
        raise MalformedSiteConfig(f"{path} is not valid JSON, fix it by hand: {error}") from error
    if not isinstance(config, dict):
        raise MalformedSiteConfig(f"{path} must hold a JSON object.")
    return config


@contextmanager
def update_common_site_config(sites_path: Path) -> Iterator[dict]:
    """Read, change and atomically write the file under its lock."""
    path = sites_path / "common_site_config.json"
    with exclusive_file_lock(path):
        config = read_common_site_config(sites_path)
        yield config
        replace_private_text_locked(path, json.dumps(config, indent=2) + "\n")
