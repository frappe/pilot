from pathlib import Path


def _read_version() -> str:
    version_file = Path(__file__).resolve().parent.parent / "VERSION"
    return version_file.read_text().strip() if version_file.exists() else "dev"


__version__ = _read_version()
is_dev_build = __version__ == "dev"
