from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from admin.backend.readers.databases import DatabaseReader, _parse_slow_query_log
from pilot.managers.mariadb import MariaDBManager
from pilot.config.mariadb_config import MariaDBConfig


def _record(second: int, sql: str) -> str:
    return (
        f"# Time: 2024-01-15T10:30:{second:02d}\n"
        "# User@Host: root[root] @ localhost []\n"
        "# Query_time: 1.5  Lock_time: 0.0 Rows_sent: 1  Rows_examined: 2\n"
        f"{sql};\n"
    )


def test_parse_slow_query_log_reads_every_field() -> None:
    content = _record(0, "SELECT 1")
    [query] = _parse_slow_query_log(content, limit=50)
    assert query.query_time == 1.5
    assert query.lock_time == 0.0
    assert query.rows_sent == 1
    assert query.rows_examined == 2
    assert query.user_host == "root[root] @ localhost []"
    assert query.sql == "SELECT 1;"


def test_parse_slow_query_log_respects_limit() -> None:
    content = "".join(_record(i, f"SELECT {i}") for i in range(10))
    queries = _parse_slow_query_log(content, limit=3)
    assert len(queries) == 3
    assert queries[-1].sql == "SELECT 9;"


def test_read_slow_queries_bounds_the_read_to_recent_records(tmp_path: Path) -> None:
    log_path = tmp_path / "slow.log"
    log_path.write_text("".join(_record(i % 60, f"SELECT {i}") for i in range(500)))

    manager = MariaDBManager(MariaDBConfig())
    reader = DatabaseReader(manager)
    with patch.object(reader, "slow_query_log_path", return_value=log_path):
        queries = reader.read_slow_queries(limit=5)

    assert len(queries) == 5
    assert queries[-1].sql == "SELECT 499;"
