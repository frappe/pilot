from __future__ import annotations

import sys
from pathlib import Path
from tempfile import TemporaryDirectory


def link_directory(source: Path, target: Path) -> None:
    """Replace a public link without removing the path between lookups."""
    from pilot.internal.atomic_file import exchange_paths

    if target.is_symlink() and target.resolve() == source.resolve():
        return
    with TemporaryDirectory(dir=target.parent, prefix=".asset-link-") as directory:
        link = Path(directory) / "link"
        link.symlink_to(source.resolve())
        if target.is_dir() and not target.is_symlink():
            exchange_paths(link, target)
        else:
            link.replace(target)


def build() -> None:
    """Use Frappe's build command with an atomic linker for public directories."""
    sys.path[0] = str(Path(__file__).resolve().parents[2])
    try:
        from frappe import bundler
    except ImportError:
        from frappe import build as bundler
    from frappe.utils.bench_helper import main

    link_assets_dir = bundler.link_assets_dir

    def link(source: str, target: str, hard_link: bool = False) -> None:
        if hard_link:
            raise ValueError("Asset retention requires symlinked Frappe assets.")
        if Path(source).is_dir():
            link_directory(Path(source), Path(target))
        else:
            link_assets_dir(source, target)

    bundler.link_assets_dir = link
    main()


if __name__ == "__main__":
    build()
