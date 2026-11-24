from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path


@dataclass
class OpenedFolder:
    """A folder the split opened instead of handing it to `du` whole."""

    path: Path
    own_bytes: int
    children: list[Path]


class DiskScan:
    """Allocated bytes of chosen folders under a root, from one parallel `du` pass.

    The root is split breadth-first into about `pieces` parts, each level opened in parallel.
    Then `workers` `du -k` processes take the parts as they become free. Their output streams
    through `keep`, so memory holds only the folders the caller asks for. Symlinks are never
    followed. Folders this user cannot read count as empty.

    The defaults suit an IOPS-capped disk like gp3: more workers only queue at the disk and
    take IOPS from the database."""

    pieces = 256
    workers = 4

    def __init__(self, root: Path) -> None:
        self.root = root

    def get_sizes(self, keep: Callable[[Path], bool]) -> dict[Path, int]:
        """Bytes of the root, of every folder the split opened, and of every folder `keep` accepts."""
        opened, parts = self.split()
        sizes = self.measure_parts(parts, keep)
        self.add_up(opened, sizes)
        return sizes

    def measure_parts(self, parts: list[Path], keep: Callable[[Path], bool]) -> dict[Path, int]:
        """Run `du` on the parts side by side; a free worker takes the next part."""
        sizes: dict[Path, int] = {}
        with ThreadPoolExecutor(self.workers) as pool:
            for part_sizes in pool.map(lambda part: self.measure_part(part, keep), parts):
                sizes.update(part_sizes)
        return sizes

    @staticmethod
    def add_up(opened: list[OpenedFolder], sizes: dict[Path, int]) -> None:
        """Size each opened folder from its children, deepest first, so a parent sees its children's totals."""
        for folder in reversed(opened):
            sizes[folder.path] = folder.own_bytes + sum(sizes.get(child, 0) for child in folder.children)

    def split(self) -> tuple[list[OpenedFolder], list[Path]]:
        """Open folders level by level until there are enough parts; parents come before children."""
        opened: list[OpenedFolder] = []
        parts: list[Path] = []
        level = [self.root]
        with ThreadPoolExecutor(self.workers) as pool:
            while level and len(level) + len(parts) < self.pieces:
                next_level: list[Path] = []
                for path, folder in zip(level, pool.map(self.open_folder, level), strict=True):
                    if folder is None:
                        parts.append(path)
                        continue
                    opened.append(folder)
                    next_level.extend(folder.children)
                level = next_level
        return opened, parts + level

    @staticmethod
    def open_folder(path: Path) -> OpenedFolder | None:
        """The folder and its children, or None when it is a file, a symlink or unreadable."""
        if path.is_symlink() or not path.is_dir():
            return None
        try:
            children = [Path(entry.path) for entry in os.scandir(path)]
            return OpenedFolder(path=path, own_bytes=path.lstat().st_blocks * 512, children=children)
        except OSError:
            return None

    @staticmethod
    def measure_part(part: Path, keep: Callable[[Path], bool]) -> dict[Path, int]:
        """The part's bytes, and those of each folder in it that `keep` accepts."""
        if part.is_symlink() or not part.is_dir():
            return {part: DiskScan.get_entry_bytes(part)}
        sizes: dict[Path, int] = {}
        # stderr is dropped, not piped: thousands of "Permission denied" lines would fill an unread pipe.
        with subprocess.Popen(
            ["du", "-k", str(part)], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL
        ) as process:
            for line in process.stdout or ():
                size, _, name = line.rstrip(b"\n").partition(b"\t")
                path = Path(os.fsdecode(name))
                if path == part or keep(path):
                    sizes[path] = int(size) * 1024
        return sizes

    @staticmethod
    def get_entry_bytes(path: Path) -> int:
        """Allocated bytes of one file or symlink; 0 when it is gone."""
        try:
            return path.lstat().st_blocks * 512
        except OSError:
            return 0
