"""Per-site backup retention stored in site_config.json."""

import json
from dataclasses import asdict
from pathlib import Path

from pilot.config.backup import BackupConfig
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked

_KEY = "backup_retention"


def read_retention(site_config_path: Path) -> BackupConfig | None:
    """The retention in site_config.json, or None."""
    try:
        block = _load(site_config_path).get(_KEY)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(block, dict):
        return None
    return BackupConfig(**{k: v for k, v in block.items() if k in set(BackupConfig().__dict__)})


def write_retention(site_config_path: Path, config: BackupConfig) -> None:
    """Save the retention in site_config.json."""
    with exclusive_file_lock(site_config_path):
        data = _load(site_config_path)
        data[_KEY] = asdict(config)
        replace_private_text_locked(site_config_path, json.dumps(data, indent=1))


def clear_retention(site_config_path: Path) -> None:
    """Remove the retention from site_config.json."""
    with exclusive_file_lock(site_config_path):
        data = _load(site_config_path)
        if data.pop(_KEY, None) is not None:
            replace_private_text_locked(site_config_path, json.dumps(data, indent=1))


def _load(path: Path) -> dict:
    """Load existing config; writers must not overwrite unreadable JSON."""
    if not path.is_file():
        return {}
    return json.loads(path.read_text())
