from __future__ import annotations

import hashlib
import os
import subprocess
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from pilot.managers.platform import is_macos

_BOOT_ID_PATH = Path("/proc/sys/kernel/random/boot_id")
_PROC_ROOT = Path("/proc")
_LAUNCH_ID_ENV = "BENCH_TASK_LAUNCH_ID"
_CAPTURE_POLL_SECONDS = 0.01
_CAPTURE_TIMEOUT_SECONDS = 1.0


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
class TaskProcessRecord:
    task_id: str
    argv: list[str]
    identity: ProcessIdentity

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "argv": self.argv,
            "identity": self.identity.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "TaskProcessRecord":
        return cls(
            task_id=str(data["task_id"]),
            argv=[str(value) for value in data["argv"]],
            identity=ProcessIdentity.from_dict(data["identity"]),
        )


@dataclass(frozen=True)
class _ProcessSnapshot:
    state: str
    pgid: int
    sid: int
    start_ticks: int
    uid: int
    argv_hash: str


class _ProcessBackend(Protocol):
    def read_process(self, pid: int) -> _ProcessSnapshot:
        pass

    def read_boot_id(self) -> str:
        pass

    def has_launch_id(self, pid: int, launch_id: str) -> bool:
        pass

    def argv_hash(self, argv: list[str]) -> str:
        pass

    def iter_pids(self) -> list[int]:
        pass


class _ProcSysBackend:
    """Reads process identity from /proc, as found on Linux."""

    def read_process(self, pid: int) -> _ProcessSnapshot:
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

    def read_boot_id(self) -> str:
        return _BOOT_ID_PATH.read_text(encoding="utf-8").strip()

    def has_launch_id(self, pid: int, launch_id: str) -> bool:
        expected = f"{_LAUNCH_ID_ENV}={launch_id}".encode()
        environment = (_PROC_ROOT / str(pid) / "environ").read_bytes().split(b"\0")
        return expected in environment

    def argv_hash(self, argv: list[str]) -> str:
        command_line = b"\0".join(os.fsencode(argument) for argument in argv) + b"\0"
        return hashlib.sha256(command_line).hexdigest()

    def iter_pids(self) -> list[int]:
        try:
            entries = _PROC_ROOT.iterdir()
        except OSError as error:
            raise OSError from error
        return [int(entry.name) for entry in entries if entry.name.isdigit()]


class _DarwinPsBackend:
    """Reads process identity via the standard macOS `ps`/`sysctl` tools.

    There is no /proc on macOS, so this shells out instead of reading kernel
    structures directly. Only single-token `ps` fields are combined into one
    call; `command` (which may contain spaces) is always fetched alone so its
    value is never ambiguous with neighboring fields.
    """

    def read_process(self, pid: int) -> _ProcessSnapshot:
        listing = self._run(["ps", "-p", str(pid), "-o", "pid=,ppid=,pgid=,stat=,uid=,start="])
        parts = listing.split()
        if len(parts) < 6:
            raise ProcessLookupError(pid)
        command = self._run(["ps", "-p", str(pid), "-o", "command="])
        if not command:
            raise ProcessLookupError(pid)
        pgid = int(parts[2])
        return _ProcessSnapshot(
            state=parts[3][0],
            pgid=pgid,
            sid=pgid,
            start_ticks=self._token_id(parts[5]),
            uid=int(parts[4]),
            argv_hash=self._hash(self._drop_executable(command)),
        )

    def read_boot_id(self) -> str:
        return self._run(["sysctl", "-n", "kern.boottime"])

    def has_launch_id(self, pid: int, launch_id: str) -> bool:
        try:
            environment = self._run(["ps", "-E", "-ww", "-p", str(pid), "-o", "command="])
        except (subprocess.CalledProcessError, OSError):
            return False
        return f"{_LAUNCH_ID_ENV}={launch_id}" in environment

    def argv_hash(self, argv: list[str]) -> str:
        return self._hash(self._drop_executable(" ".join(argv)))

    def iter_pids(self) -> list[int]:
        try:
            listing = self._run(["ps", "-A", "-o", "pid="])
        except (subprocess.CalledProcessError, OSError) as error:
            raise OSError(str(error)) from error
        return [int(token) for token in listing.split()]

    @staticmethod
    def _drop_executable(command: str) -> str:
        """Strip the leading executable token.

        macOS framework Python builds re-exec themselves into a different
        binary path (e.g. venv's `bin/python` launches as
        `Python.app/Contents/MacOS/Python`), so comparing the interpreter
        path itself is unreliable. The remaining arguments are specific
        enough to detect drift, backed by the launch-id environment check.
        """
        return command.partition(" ")[2]

    @staticmethod
    def _hash(rendered: str) -> str:
        return hashlib.sha256(rendered.encode()).hexdigest()

    @staticmethod
    def _token_id(token: str) -> int:
        return int.from_bytes(hashlib.sha256(token.encode()).digest()[:8], "big")

    @staticmethod
    def _run(argv: list[str]) -> str:
        result = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            env={**os.environ, "LC_ALL": "C"},
        )
        if result.returncode != 0 or not result.stdout.strip():
            raise ProcessLookupError(" ".join(argv))
        return result.stdout.strip()


def _default_backend() -> _ProcessBackend:
    return _DarwinPsBackend() if is_macos() else _ProcSysBackend()


class ProcessInspector:
    def __init__(self, backend: _ProcessBackend | None = None) -> None:
        self._backend = backend or _default_backend()

    def capture(
        self,
        pid: int,
        expected_argv: list[str],
        launch_id: str,
    ) -> ProcessIdentity:
        deadline = time.monotonic() + _CAPTURE_TIMEOUT_SECONDS
        while True:
            try:
                return self._capture_once(pid, expected_argv, launch_id)
            except RuntimeError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(_CAPTURE_POLL_SECONDS)

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
        for pid in self._iter_pids():
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

    def has_pid(self, identity: ProcessIdentity, pid: int) -> bool:
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
        try:
            pids = self._iter_pids()
        except OSError:
            return ProcessOwnership.UNKNOWN

        states = self._inspect_group_states(pids, identity)
        if ProcessOwnership.OWNED in states:
            return ProcessOwnership.OWNED
        if ProcessOwnership.UNKNOWN in states:
            return ProcessOwnership.UNKNOWN
        return ProcessOwnership.STALE if ProcessOwnership.STALE in states else ProcessOwnership.DEAD

    def _inspect_group_states(
        self,
        pids: list[int],
        identity: ProcessIdentity,
    ) -> list[ProcessOwnership]:
        states = []
        for pid in pids:
            state = self._inspect_group_entry(pid, identity)
            if state is not None:
                states.append(state)
        return states

    def _inspect_group_entry(self, pid: int, identity: ProcessIdentity) -> ProcessOwnership | None:
        snapshot, failed_unknown = self._process_snapshot(pid)
        if snapshot is None:
            return ProcessOwnership.UNKNOWN if failed_unknown else None
        if snapshot.state == "Z":
            return None

        has_launch_id, launch_unknown = self._snapshot_launch_id(pid, snapshot, identity)
        if has_launch_id:
            return ProcessOwnership.OWNED
        if snapshot.pgid != identity.pgid:
            return None
        if launch_unknown:
            return ProcessOwnership.UNKNOWN
        return ProcessOwnership.STALE

    def _process_snapshot(self, pid: int) -> tuple[_ProcessSnapshot | None, bool]:
        try:
            return self._read_process(pid), False
        except (FileNotFoundError, ProcessLookupError):
            return None, False
        except (PermissionError, OSError, ValueError):
            return None, True

    def _snapshot_launch_id(
        self,
        pid: int,
        snapshot: _ProcessSnapshot,
        identity: ProcessIdentity,
    ) -> tuple[bool, bool]:
        if snapshot.uid != identity.uid:
            return False, False
        try:
            return self._has_launch_id(pid, identity.launch_id), False
        except (PermissionError, OSError):
            return False, True

    def _read_process(self, pid: int) -> _ProcessSnapshot:
        return self._backend.read_process(pid)

    def _read_boot_id(self) -> str:
        return self._backend.read_boot_id()

    def _has_launch_id(self, pid: int, launch_id: str) -> bool:
        return self._backend.has_launch_id(pid, launch_id)

    def _argv_hash(self, argv: list[str]) -> str:
        return self._backend.argv_hash(argv)

    def _iter_pids(self) -> list[int]:
        return self._backend.iter_pids()
