from __future__ import annotations

import shutil
from collections.abc import Callable
from pathlib import Path


class MoveJournal:
    """Moves files and remembers each move, so a failed import can put everything back."""

    def __init__(self) -> None:
        self.moves: list[tuple[Path, Path]] = []

    def move(self, source: Path, target: Path) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.rmdir()  # an empty placeholder; anything else stops the move
        shutil.move(source, target)
        self.moves.append((source, target))

    def undo(self, keep: int = 0, on_progress: Callable[[str], None] = lambda message: None) -> None:
        """Reverse every move after the first `keep`. What Pilot rebuilt at a source makes way."""
        while len(self.moves) > keep:
            source, target = self.moves.pop()
            if not target.exists() and not target.is_symlink():
                on_progress(f"  Missing {target}, left as it is.")
                continue
            if source.is_dir() and not source.is_symlink():
                shutil.rmtree(source)
            elif source.exists() or source.is_symlink():
                source.unlink()
            source.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(target, source)
