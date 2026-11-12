"""Deadlocks that InnoDB dumps to the MariaDB error log (innodb_print_all_deadlocks)."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

# Deadlocks are wanted recent, and the log can be large, so only its end is read.
TAIL_BYTES = 8 * 1024 * 1024

_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) +\d+ \[\w+\] InnoDB: ?")
_START = "Transactions deadlock detected"
_TRANSACTION = re.compile(r"^\*\*\* \((\d+)\) TRANSACTION:")
_TRANSACTION_ID = re.compile(r"^TRANSACTION (\d+),")
_THREAD = re.compile(r"^MariaDB thread id (\d+),")
_WAITING = re.compile(r"^\*\*\* (\(\d+\) )?WAITING FOR THIS LOCK")
_LOCK = re.compile(
    r"(?:index (\S+) of )?table `([^`]+)`\.`([^`]+)` trx id \d+ lock[ _]mode (.+?)(?: waiting)?$"
)
_ROLLBACK = re.compile(r"^\*\*\* WE ROLL BACK TRANSACTION \((\d+)\)")


@dataclass
class DeadlockTransaction:
    number: int
    transaction_id: str = ""
    thread_id: int | None = None
    query: str = ""
    database: str = ""
    table: str = ""
    index: str = ""
    lock_mode: str = ""
    victim: bool = False


@dataclass
class Deadlock:
    """`time` is UTC ISO, or empty when the dump had no timestamp."""

    time: str
    transactions: list[DeadlockTransaction] = field(default_factory=list)

    @property
    def databases(self) -> set[str]:
        return {transaction.database for transaction in self.transactions if transaction.database}

    @property
    def moment(self) -> datetime | None:
        return datetime.fromisoformat(self.time) if self.time else None

    def is_match(
        self, database: str = "", since: datetime | None = None, until: datetime | None = None
    ) -> bool:
        if database and database not in self.databases:
            return False
        if not since and not until:
            return True
        moment = self.moment
        if moment is None:
            return False
        return not (since and moment < since) and not (until and moment > until)


@dataclass
class DeadlockReport:
    """`log_path` is None when the server keeps no error log file this host can read."""

    log_path: str | None
    print_all_deadlocks: bool
    deadlocks: list[Deadlock] = field(default_factory=list)


class DeadlockLog:
    """The deadlock dumps in a MariaDB error log, newest first."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def get_deadlocks(
        self,
        database: str = "",
        limit: int = 50,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> list[Deadlock]:
        deadlocks = DeadlockParser().parse(self.read_tail())
        deadlocks = [deadlock for deadlock in deadlocks if deadlock.is_match(database, since, until)]
        return deadlocks[::-1][:limit]

    def read_tail(self) -> str:
        with self.path.open("rb") as log:
            size = log.seek(0, 2)
            log.seek(max(0, size - TAIL_BYTES))
            return log.read().decode("utf-8", errors="replace")


class DeadlockParser:
    """Reads deadlock dumps line by line. A dump runs from 'Transactions deadlock detected'
    to 'WE ROLL BACK TRANSACTION (n)'; other log lines are skipped."""

    def __init__(self) -> None:
        self.deadlocks: list[Deadlock] = []
        self.current: Deadlock | None = None
        self.reading_query = False
        self.waiting_section = False

    def parse(self, text: str) -> list[Deadlock]:
        for raw in text.splitlines():
            stamp = _PREFIX.match(raw)
            line = (raw[stamp.end() :] if stamp else raw).strip()
            if _START in line:
                self.current = Deadlock(time=server_time_to_utc(stamp[1]) if stamp else "")
                self.deadlocks.append(self.current)
            elif self.current is not None:
                self.read_dump_line(self.current, line)
        return [deadlock for deadlock in self.deadlocks if deadlock.transactions]

    def read_dump_line(self, deadlock: Deadlock, line: str) -> None:
        if line.startswith("***"):
            self.read_section(deadlock, line)
        elif deadlock.transactions:
            self.read_transaction_line(deadlock.transactions[-1], line)

    def read_section(self, deadlock: Deadlock, line: str) -> None:
        self.reading_query = False
        self.waiting_section = bool(_WAITING.match(line))
        if match := _TRANSACTION.match(line):
            deadlock.transactions.append(DeadlockTransaction(number=int(match[1])))
        elif match := _ROLLBACK.match(line):
            for transaction in deadlock.transactions:
                transaction.victim = transaction.number == int(match[1])
            self.current = None

    def read_transaction_line(self, transaction: DeadlockTransaction, line: str) -> None:
        if match := _TRANSACTION_ID.match(line):
            transaction.transaction_id = match[1]
        elif match := _THREAD.match(line):
            transaction.thread_id = int(match[1])
            self.reading_query = True
        elif self.reading_query:
            transaction.query = f"{transaction.query}\n{line}".strip()
        elif self.waiting_section and not transaction.table and (match := _LOCK.search(line)):
            index, transaction.database, transaction.table, transaction.lock_mode = match.groups()
            transaction.index = index or ""


def server_time_to_utc(stamp: str) -> str:
    """The error log stamps the server's local time; a naive astimezone() reads it in the host zone."""
    return datetime.strptime(stamp, "%Y-%m-%d %H:%M:%S").astimezone(UTC).isoformat()
