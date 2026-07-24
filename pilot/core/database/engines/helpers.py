from __future__ import annotations

from pathlib import Path

from pilot.core.database.base import QueryResult
from pilot.exceptions import DatabaseError

# Cap on rows returned by a single query, so an unbounded SELECT cannot
# exhaust memory. Callers surface the overflow as QueryResult.truncated.
MAX_ROWS = 5000


def rows_as_dicts(result: QueryResult) -> list[dict]:
    return [dict(zip(result.columns, row, strict=True)) for row in result.rows]


def validated_process_id(process_id: int) -> int:
    """Neither KILL nor pg_terminate_backend take placeholders here, so the id
    is interpolated - reject anything that is not a positive integer."""
    if isinstance(process_id, bool) or not isinstance(process_id, int):
        raise DatabaseError(f"Process id must be an integer, got {process_id!r}")
    if process_id <= 0:
        raise DatabaseError(f"Process id must be positive, got {process_id}")
    return process_id


def file_modified_ms(path: Path) -> int | None:
    """Best-effort: the server may be remote or its datadir unreadable."""
    try:
        return int(path.stat().st_mtime * 1000)
    except OSError:
        return None


def is_local_host(host: str, socket: str | None = None) -> bool:
    return bool(socket) or host in ("localhost", "127.0.0.1", "::1", "")


def disk_free(path: str) -> int | None:
    """Best-effort: the data directory may not be readable from this process."""
    import shutil

    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None
