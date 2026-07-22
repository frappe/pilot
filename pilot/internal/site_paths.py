from pathlib import Path

from pilot.internal.validators import validate_site_name


def resolve_site_path(bench_root: Path, name: str) -> Path | None:
    """Resolve a site path without following symlinks outside sites/."""
    raw_sites_path = bench_root / "sites"
    if raw_sites_path.is_symlink():
        return None
    sites_path = raw_sites_path.resolve()
    site_path = sites_path / name
    if site_path.is_symlink() or site_path.resolve(strict=False).parent != sites_path:
        return None
    return site_path


def site_config_path(bench_root: Path, name: str) -> Path | None:
    if validate_site_name(name):
        return None
    site_path = resolve_site_path(bench_root, name)
    if site_path is None:
        return None
    config_path = site_path / "site_config.json"
    if config_path.is_symlink() or not config_path.is_file():
        return None
    return config_path


def site_exists(bench_root: Path, name: str) -> bool:
    return site_config_path(bench_root, name) is not None
