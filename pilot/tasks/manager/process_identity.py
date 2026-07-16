from __future__ import annotations

import hashlib
import os
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from pilot.tasks.timing import CAPTURE_POLL_SECONDS, CAPTURE_TIMEOUT_SECONDS

_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_PROC_ROOT = Path("/proc")
_LAUNCH_ID_ENV = "BENCH_TASK_LAUNCH_ID"


class ProcessOwnership(StrEnum):
    OWNED = "owned"
    DEAD = "dead"
    STALE = "stale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    pgid: int
    sid: int
    boot_id: str
    start_ticks: int
    uid: int
    argv_hash: str
    launch_id: str

    def to_dict(self) -> dict[str, int | str]:
        return {
            "pid": self.pid,
            "pgid": self.pgid,
            "sid": self.sid,
            "boot_id": self.boot_id,
            "start_ticks": self.start_ticks,
            "uid": self.uid,
            "argv_hash": self.argv_hash,
            "launch_id": self.launch_id,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ProcessIdentity:
        return cls(
            pid=int(data["pid"]),
            pgid=int(data["pgid"]),
            sid=int(data["sid"]),
            boot_id=str(data["boot_id"]),
            start_ticks=int(data["start_ticks"]),
            uid=int(data["uid"]),
            argv_hash=str(data["argv_hash"]),
            launch_id=str(data["launch_id"]),
        )


@dataclass(frozen=True)
class _ProcessSnapshot:
    state: str
    pgid: int
    sid: int
    start_ticks: int
    uid: int
    argv_hash: str


class ProcessInspector:
    def capture(
        self,
        pid: int,
        expected_argv: list[str],
        launch_id: str,
    ) -> ProcessIdentity:
        deadline = time.monotonic() + CAPTURE_TIMEOUT_SECONDS
        while True:
            try:
                return self._capture_once(pid, expected_argv, launch_id)
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(CAPTURE_POLL_SECONDS)

    def _capture_once(
        self,
        pid: int,
        expected_argv: list[str],
        launch_id: str,
    ) -> ProcessIdentity:
        snapshot = self._read_process(pid)
        if snapshot.state == "Z":
            raise ProcessLookupError(pid)
        if snapshot.pgid != pid or snapshot.sid != pid:
            raise RuntimeError(f"Task wrapper {pid} is not a process-group leader")
        if snapshot.argv_hash != self._argv_hash(expected_argv):
            raise RuntimeError(f"Task wrapper {pid} has unexpected arguments")
        if not self._has_launch_id(pid, launch_id):
            raise RuntimeError(f"Task wrapper {pid} has no launch identity")
        return ProcessIdentity(
            pid=pid,
            pgid=snapshot.pgid,
            sid=snapshot.sid,
            boot_id=self._read_boot_id(),
            start_ticks=snapshot.start_ticks,
            uid=snapshot.uid,
            argv_hash=snapshot.argv_hash,
            launch_id=launch_id,
        )

    def inspect(
        self,
        identity: ProcessIdentity,
        expected_argv: list[str],
    ) -> ProcessOwnership:
        try:
            if identity.argv_hash != self._argv_hash(expected_argv):
                return ProcessOwnership.STALE
            if identity.boot_id != self._read_boot_id():
                return ProcessOwnership.STALE
            snapshot = self._read_process(identity.pid)
        except (FileNotFoundError, ProcessLookupError):
            return self._inspect_group(identity)
        except (PermissionError, OSError, ValueError):
            return ProcessOwnership.UNKNOWN

        if snapshot.state == "Z":
            return self._inspect_group(identity)
        if (
            snapshot.pgid != identity.pgid
            or snapshot.sid != identity.sid
            or snapshot.start_ticks != identity.start_ticks
            or snapshot.uid != identity.uid
            or snapshot.argv_hash != identity.argv_hash
        ):
            return ProcessOwnership.STALE
        try:
            return (
                ProcessOwnership.OWNED
                if self._has_launch_id(identity.pid, identity.launch_id)
                else ProcessOwnership.STALE
            )
        except (PermissionError, OSError):
            return ProcessOwnership.UNKNOWN

    def owned_pids(self, identity: ProcessIdentity) -> set[int]:
        if identity.boot_id != self._read_boot_id():
            return set()
        owned = set()
        for entry in _PROC_ROOT.iterdir():
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                snapshot = self._read_process(pid)
                if (
                    snapshot.state != "Z"
                    and snapshot.uid == identity.uid
                    and self._has_launch_id(pid, identity.launch_id)
                ):
                    owned.add(pid)
            except (FileNotFoundError, ProcessLookupError, PermissionError, OSError, ValueError):
                continue
        return owned

    def owns_pid(self, identity: ProcessIdentity, pid: int) -> bool:
        try:
            snapshot = self._read_process(pid)
            return (
                snapshot.state != "Z"
                and snapshot.uid == identity.uid
                and self._has_launch_id(pid, identity.launch_id)
            )
        except (FileNotFoundError, ProcessLookupError, PermissionError, OSError, ValueError):
            return False

    def _inspect_group(self, identity: ProcessIdentity) -> ProcessOwnership:
        matching_group = False
        unknown = False
        try:
            entries = list(_PROC_ROOT.iterdir())
        except OSError:
            return ProcessOwnership.UNKNOWN
        for entry in entries:
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            try:
                snapshot = self._read_process(pid)
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (PermissionError, OSError, ValueError):
                unknown = True
                continue
            if snapshot.state == "Z":
                continue
            if snapshot.uid == identity.uid:
                try:
                    if self._has_launch_id(pid, identity.launch_id):
                        return ProcessOwnership.OWNED
                except (PermissionError, OSError):
                    if snapshot.pgid == identity.pgid:
                        unknown = True
            if snapshot.pgid == identity.pgid:
                matching_group = True
        if unknown:
            return ProcessOwnership.UNKNOWN
        return ProcessOwnership.STALE if matching_group else ProcessOwnership.DEAD

    def _read_process(self, pid: int) -> _ProcessSnapshot:
        process_dir = _PROC_ROOT / str(pid)
        stat_text = (process_dir / "stat").read_text(encoding="utf-8")
        fields = stat_text[stat_text.rfind(")") + 2 :].split()
        if len(fields) < 20:
            raise ValueError(f"Invalid process stat for {pid}")
        return _ProcessSnapshot(
            state=fields[0],
            pgid=int(fields[2]),
            sid=int(fields[3]),
            start_ticks=int(fields[19]),
            uid=process_dir.stat().st_uid,
            argv_hash=hashlib.sha256((process_dir / "cmdline").read_bytes()).hexdigest(),
        )

    def _has_launch_id(self, pid: int, launch_id: str) -> bool:
        expected = f"{_LAUNCH_ID_ENV}={launch_id}".encode()
        environment = (_PROC_ROOT / str(pid) / "environ").read_bytes().split(b"\0")
        return expected in environment

    @staticmethod
    def _read_boot_id() -> str:
        return _BOOT_ID_PATH.read_text(encoding="utf-8").strip()

    @staticmethod
    def _argv_hash(argv: list[str]) -> str:
        command_line = b"\0".join(os.fsencode(argument) for argument in argv) + b"\0"
        return hashlib.sha256(command_line).hexdigest()
