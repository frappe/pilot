"""Bench-wide, append-only audit log. Entries are typed (e.g. "backup") and
sharded into one file per ISO week (``audit_<year>_<week>.jsonl``) so each file
stays bounded and old weeks can be archived or dropped independently. Entries
outlive the sites they record."""

import json
import re
from datetime import datetime, timezone

from pilot.secure_files import open_private

_FILE_RE = re.compile(r"^audit_\d{4}_\d{2}\.jsonl$")


class AuditLog:
    def __init__(self, bench) -> None:
        self._dir = bench.logs_path

    def append(self, entry_type: str, entry: dict) -> None:
        record = {"type": entry_type, "logged_at": self._now(), **entry}
        self._dir.mkdir(parents=True, exist_ok=True)
        with open_private(self._current_file(), "a") as handle:
            handle.write(json.dumps(record) + "\n")

    def entries(self, entry_type=None, site=None, status=None, limit=None) -> list[dict]:
        """Matching records across all weekly files, newest first. Reads lazily, so
        a small ``limit`` never touches files (or lines) beyond what it returns."""
        matched = []
        for record in self._read_newest_first():
            if self._matches(record, entry_type, site, status):
                matched.append(record)
                if limit is not None and len(matched) >= limit:
                    break
        return matched

    def _current_file(self):
        year, week, _ = datetime.now(timezone.utc).isocalendar()
        return self._dir / f"audit_{year}_{week:02d}.jsonl"

    def _weekly_files(self) -> list:
        if not self._dir.is_dir():
            return []
        files = [p for p in self._dir.iterdir() if _FILE_RE.match(p.name)]
        return sorted(files, key=lambda p: p.name, reverse=True)  # zero-padded, so name sort == time sort

    def _read_newest_first(self):
        for path in self._weekly_files():
            for line in self._reversed_lines(path):
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue

    @staticmethod
    def _reversed_lines(path, chunk_size: int = 65536):
        """Yield a file's non-empty lines last-first, reading it back-to-front in
        chunks so an entire (potentially large) weekly file is never held in memory."""
        with path.open("rb") as handle:
            handle.seek(0, 2)
            pointer = handle.tell()
            tail = b""
            while pointer > 0:
                step = min(chunk_size, pointer)
                pointer -= step
                handle.seek(pointer)
                lines = (handle.read(step) + tail).split(b"\n")
                tail = lines.pop(0)  # may be a partial line completed by the next (earlier) chunk
                for line in reversed(lines):
                    if line:
                        yield line.decode()
            if tail:
                yield tail.decode()

    @staticmethod
    def _matches(record: dict, entry_type, site, status) -> bool:
        return (
            (entry_type is None or record.get("type") == entry_type)
            and (site is None or record.get("site") == site)
            and (status is None or record.get("status") == status)
        )

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()
