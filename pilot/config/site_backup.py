"""Per-site backup retention, stored in the site's site_config.json under
``backup_retention``. Present only while automated backups are enabled; when
absent, nothing is pruned (every backup is kept)."""

import json
from dataclasses import asdict
from pathlib import Path

from pilot.config.backup import BackupConfig
from pilot.secure_files import write_private_text

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
    """Existing config as a dict. Raises on a corrupt/unreadable file so writers
    never overwrite (and erase) a config they couldn't parse; readers catch it."""
    if not path.is_file():
        return {}
    return json.loads(path.read_text())
