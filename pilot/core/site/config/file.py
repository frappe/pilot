from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pilot.core.site.config.public import PublicConfig
from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked


class SiteConfigFile:
    """A site's site_config.json. Writes hold the file lock and refuse a path that leaves the bench."""

    def __init__(self, sites_root: Path, site_name: str) -> None:
        self.sites_root = sites_root
        self.site_name = site_name

    @property
    def path(self) -> Path:
        """Where the file is, without any safety check; use for reads."""
        return self.sites_root / self.site_name / "site_config.json"

    @property
    def safe_path(self) -> Path:
        """The config path, after checking that no symbolic link points it outside the sites folder."""
        if self.sites_root.is_symlink():
            raise BenchError("Site configuration path must stay within the bench.")
        resolved_root = self.sites_root.resolve()
        site_path = resolved_root / self.site_name
        config_path = site_path / "site_config.json"
        if (
            site_path.is_symlink()
            or site_path.resolve(strict=False).parent != resolved_root
            or config_path.is_symlink()
            or not config_path.is_file()
        ):
            raise BenchError("Site configuration is unavailable.")
        return config_path

    def read(self) -> dict:
        """The whole config. Fails when the file is not a JSON object."""
        config = json.loads(self.path.read_text())
        if not isinstance(config, dict):
            raise ValueError("Site configuration must be a JSON object.")
        return config

    @contextmanager
    def updating(self) -> Iterator[dict]:
        """Yield the config to change in place; it is written back only when it changed."""
        config_path = self.safe_path
        with exclusive_file_lock(config_path):
            config = json.loads(config_path.read_text())
            original = json.dumps(config, sort_keys=True)
            yield config
            if json.dumps(config, sort_keys=True) != original:
                replace_private_text_locked(config_path, json.dumps(config, indent=1))

    def set_values(self, values: dict) -> None:
        """Write these keys, keeping every other key as it is."""
        with self.updating() as config:
            config.update(values)

    def clear_certificate_pin(self) -> None:
        """Forget the pinned certificate lineage so the site gets one under its own name."""
        with self.updating() as config:
            config.pop("cert_name", None)

    def read_public(self) -> dict:
        """The config as an Admin caller may see it."""
        return PublicConfig(self.read()).visible

    def update_public(self, patch: dict) -> dict:
        """Apply an Admin patch and return what the caller may now read."""
        with exclusive_file_lock(self.path):
            current = self.read()
            public = PublicConfig(current)
            if error := public.get_patch_error(patch):
                raise BenchError(error)
            merged = public.merge(patch)
            replace_private_text_locked(self.path, json.dumps(merged, indent=1))
        return PublicConfig(merged).visible
