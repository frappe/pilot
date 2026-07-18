"""Per-site backup retention stored in site_config.json."""

import json
from dataclasses import asdict
from pathlib import Path

from pilot.config.backup import BackupConfig
from pilot.utils import write_private_text

_KEY = "backup_retention"
_FIELDS = set(BackupConfig().__dict__)


def read_retention(site_config_path: Path) -> BackupConfig | None:
    try:
        block = _load(site_config_path).get(_KEY)
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(block, dict):
        return None
    return BackupConfig(**{k: v for k, v in block.items() if k in _FIELDS})


def write_retention(site_config_path: Path, config: BackupConfig) -> None:
    data = _load(site_config_path)
    data[_KEY] = asdict(config)
    write_private_text(site_config_path, json.dumps(data, indent=1))


def clear_retention(site_config_path: Path) -> None:
    data = _load(site_config_path)
    if data.pop(_KEY, None) is not None:
        write_private_text(site_config_path, json.dumps(data, indent=1))


def _load(path: Path) -> dict:
    """Load existing config; writers must not overwrite unreadable JSON."""
    if not path.is_file():
        return {}
    return json.loads(path.read_text())
