from __future__ import annotations

from pathlib import Path

from pilot.core.database.slow_log import SlowLogFile

LOG = """/usr/sbin/mariadbd, Version: 11.8.3-MariaDB (MariaDB Server). started with:
Tcp port: 3310  Unix socket: /run/mysqld.sock
Time		    Id Command	Argument
# Time: 261112 13:20:01
# User@Host: one[one] @ localhost []
# Thread_id: 55  Schema: site_one  QC_hit: No
# Query_time: 2.000213  Lock_time: 0.000010  Rows_sent: 1  Rows_examined: 40
# Rows_affected: 0  Bytes_sent: 64
use site_one;
SET timestamp=1794489601;
SELECT * FROM `tabUser` WHERE name = 'a';
# User@Host: one[one] @ localhost []
# Thread_id: 56  Schema: site_one  QC_hit: No
# Query_time: 1.5  Lock_time: 0.0  Rows_sent: 1  Rows_examined: 10
SET timestamp=1794489602;
SELECT * FROM `tabUser` WHERE name = 'b';
/usr/sbin/mariadbd, Version: 11.8.3-MariaDB (MariaDB Server). started with:
# User@Host: two[two] @ localhost []
# Thread_id: 57  Schema: site_two  QC_hit: No
# Query_time: 1.1  Lock_time: 0.0  Rows_sent: 0  Rows_examined: 5
SET timestamp=1794489603;
DELETE FROM `tabLog`;
"""


def test_entries_group_by_normalized_query_and_filter_by_database(tmp_path: Path) -> None:
    path = tmp_path / "slow.log"
    path.write_text(LOG)

    [user, log] = SlowLogFile(path).get_top_queries()
    assert (user.database, user.calls, user.duration_seconds) == ("site_one", 2, 3.5)
    assert (user.rows_examined, user.rows_sent) == (50, 2)
    assert user.query == "SELECT * FROM `tabUser` WHERE name = ?;"
    assert user.example == "SELECT * FROM `tabUser` WHERE name = 'a';"
    assert log.query == "DELETE FROM `tabLog`;"
    assert [query.database for query in SlowLogFile(path).get_top_queries("site_two")] == ["site_two"]
