from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from pathlib import Path

from pilot.internal.atomic_file import exclusive_file_lock, replace_private_text_locked
from pilot.utils import make_private_directory


class SiteCreationOwner:
    """Serialize Pilot site creation and identify which task may clean it up."""

    def __init__(self, bench_root: Path, site: str) -> None:
        if not site or Path(site).name != site:
            raise ValueError("Site creation ownership must stay within the bench.")
        self.path = bench_root / "config" / "site-creations" / f"{site}.json"

    @contextlib.contextmanager
    def locked(self) -> Iterator[SiteCreationOwner]:
        make_private_directory(self.path.parent, parents=True)
        with exclusive_file_lock(self.path):
            yield self

    def claim(self, task_id: str) -> None:
        if task_id:
            replace_private_text_locked(self.path, json.dumps({"task_id": task_id}))
        else:
            self.path.unlink(missing_ok=True)

    def is_owned_by(self, task_id: str) -> bool:
        if not task_id:
            return False
        try:
            return json.loads(self.path.read_text()).get("task_id") == task_id
        except FileNotFoundError:
            return False

    def release(self, task_id: str) -> None:
        if self.is_owned_by(task_id):
            self.path.unlink()
