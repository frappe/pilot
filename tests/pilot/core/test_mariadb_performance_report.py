"""Tests for MariaDBPerformanceReport's scoping, paging and row mapping."""

from __future__ import annotations

from unittest.mock import MagicMock

from pilot.core.database.engines.mariadb_performance import (
    FRAMEWORK_INDEXES,
    FULL_TABLE_SCAN_QUERIES,
    REDUNDANT_INDEXES,
    SYSTEM_SCHEMAS,
    TIME_CONSUMING_QUERIES,
    UNUSED_INDEXES,
    MariaDBPerformanceReport,
)


class FakeCursor:
    """Records executed statements and replays queued result sets in order."""

    def __init__(self, results: list[list[dict]]) -> None:
        self.results = list(results)
        self.calls: list[tuple[str, dict]] = []
        self._current: list[dict] = []

    def execute(self, query, parameters=None):
        self.calls.append((query, parameters or {}))
        self._current = self.results.pop(0) if self.results else []

    def fetchone(self):
        return self._current[0] if self._current else None

    def fetchall(self):
        return self._current

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


def _connect(cursor: FakeCursor):
    connection = MagicMock()
    connection.cursor.return_value = cursor
    return lambda: connection


def _unused_index_row(index: str) -> dict:
    return {"db": "site_db", "table_name": "tabUser", "index_name": index}


def test_site_scope_binds_the_database_name_instead_of_interpolating_it() -> None:
    cursor = FakeCursor([[]])
    MariaDBPerformanceReport(_connect(cursor), "site_db").get_unused_indexes()

    query, parameters = cursor.calls[-1]
    assert "site_db" not in query
    assert parameters["database"] == "site_db"


def test_server_scope_excludes_system_schemas() -> None:
    cursor = FakeCursor([[]])
    MariaDBPerformanceReport(_connect(cursor), "").get_unused_indexes()

    query, parameters = cursor.calls[-1]
    assert "%(system_schemas)s" in query
    assert parameters["system_schemas"] == SYSTEM_SCHEMAS
    assert parameters["database"] == ""


def test_each_section_executes_its_statement_verbatim_without_building_sql() -> None:
    report = MariaDBPerformanceReport
    for method, statement in (
        ("get_time_consuming_queries", TIME_CONSUMING_QUERIES),
        ("get_full_table_scan_queries", FULL_TABLE_SCAN_QUERIES),
        ("get_unused_indexes", UNUSED_INDEXES),
        ("get_redundant_indexes", REDUNDANT_INDEXES),
    ):
        cursor = FakeCursor([[]])
        getattr(report(_connect(cursor), "site_db"), method)()
        assert [query for query, _ in cursor.calls] == [statement], method


def test_unused_indexes_exclude_framework_indexes() -> None:
    cursor = FakeCursor([[]])
    MariaDBPerformanceReport(_connect(cursor), "site_db").get_unused_indexes()

    query, parameters = cursor.calls[-1]
    assert "framework_indexes" in query
    assert parameters["framework_indexes"] == FRAMEWORK_INDEXES


def test_a_page_reads_one_row_past_it_to_answer_has_next_page() -> None:
    cursor = FakeCursor([[_unused_index_row(f"idx_{n}") for n in range(3)]])
    section = MariaDBPerformanceReport(_connect(cursor), "site_db").get_unused_indexes(limit=2)

    _, parameters = cursor.calls[-1]
    assert parameters["limit"] == 3
    assert len(section.data) == 2
    assert section.has_next_page is True


def test_a_short_page_has_no_next_page() -> None:
    cursor = FakeCursor([[_unused_index_row("idx_0")]])
    section = MariaDBPerformanceReport(_connect(cursor), "site_db").get_unused_indexes(limit=2)

    assert len(section.data) == 1
    assert section.has_next_page is False


def test_offset_is_bound_rather_than_interpolated() -> None:
    cursor = FakeCursor([[]])
    MariaDBPerformanceReport(_connect(cursor), "site_db").get_unused_indexes(limit=10, offset=40)

    query, parameters = cursor.calls[-1]
    assert "OFFSET %(offset)s" in query
    assert parameters["offset"] == 40


def test_sections_map_their_rows() -> None:
    cursor = FakeCursor(
        [
            [
                {
                    "db": "site_db",
                    "query": "SELECT 1",
                    "percent": "42.5",
                    "calls": "3",
                    "average_time_ms": "1.5",
                    "total_time_ms": "4.5",
                }
            ]
        ]
    )
    section = MariaDBPerformanceReport(_connect(cursor), "site_db").get_time_consuming_queries()

    assert section.data[0].percent == 42.5
    assert section.data[0].calls == 3


def test_null_counters_become_zero_rather_than_failing() -> None:
    cursor = FakeCursor(
        [
            [
                {
                    "db": "site_db",
                    "query": "SELECT 1",
                    "percent": None,
                    "calls": None,
                    "average_time_ms": None,
                    "total_time_ms": None,
                }
            ]
        ]
    )
    section = MariaDBPerformanceReport(_connect(cursor), "").get_time_consuming_queries()

    assert section.data[0].percent == 0.0
    assert section.data[0].calls == 0
