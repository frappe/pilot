from __future__ import annotations

import shlex
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from pilot.exceptions import CronError

_MARKER_PREFIX = "# bench-cron:"


def cron_module_command(module: str, arguments: list, log_file: Path) -> str:
    """A `python -m <module>` line safe to run from cron.

    Cron starts in the user's home directory, which holds the source tree. That directory shadows
    the real `pilot` package as an empty namespace package, so PYTHONPATH pins the source root.
    `pilot` itself has no third-party dependencies, so a path is all any interpreter needs.
    """
    from pilot.utils import cli_root

    parts = [
        f"PYTHONPATH={shlex.quote(str(cli_root()))}",
        shlex.quote(sys.executable),
        "-m",
        module,
        *(shlex.quote(str(argument)) for argument in arguments),
    ]
    return f"{' '.join(parts)} >> {shlex.quote(str(log_file))} 2>&1"


class CronManager:
    """One marked cron entry per (bench, job_key)."""

    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def get_schedule(self, job_key: str) -> str | None:
        lines = self._read_crontab()
        try:
            i = lines.index(self._marker(job_key))
            parts = lines[i + 1].split()
            return " ".join(parts[:5]) if len(parts) >= 5 else None
        except (ValueError, IndexError):
            return None

    def set_schedule(self, job_key: str, cron_expr: str, command: str) -> None:
        with self._lock():
            lines = self._read_crontab()
            marker = self._marker(job_key)
            entry = f"{cron_expr} {command}"
            try:
                i = lines.index(marker)
            except ValueError:
                lines += [marker, entry]
            else:
                if i + 1 < len(lines):
                    lines[i + 1] = entry
                else:
                    lines.append(entry)
            self._write_crontab(lines)

    def remove_schedule(self, job_key: str) -> None:
        with self._lock():
            lines = self._read_crontab()
            marker = self._marker(job_key)
            try:
                i = lines.index(marker)
                del lines[i : i + 2]
            except ValueError:
                pass
            self._write_crontab(lines)

    def _marker(self, job_key: str) -> str:
        return f"{_MARKER_PREFIX}{self._bench_root}:{job_key}"

    @contextmanager
    def _lock(self) -> Iterator[None]:
        """Every bench and the registry cache share the host user's one crontab."""
        from pilot.internal.atomic_file import exclusive_file_lock
        from pilot.utils import cli_root

        lock_target = cli_root() / "system" / "crontab"
        lock_target.parent.mkdir(parents=True, exist_ok=True)
        with exclusive_file_lock(lock_target):
            yield

    def _read_crontab(self) -> list[str]:
        result = self._crontab("-l")
        if result.returncode == 0:
            return result.stdout.splitlines()
        # An empty crontab is an error exit; anything else must not be read as empty and overwritten.
        if "no crontab" in result.stderr.lower():
            return []
        raise CronError(f"Could not read the crontab: {result.stderr.strip()}")

    def _write_crontab(self, lines: list[str]) -> None:
        non_empty = [line for line in lines if line.strip()]
        if not non_empty:
            self._crontab("-r")
            return
        result = self._crontab("-", content="\n".join(non_empty) + "\n")
        if result.returncode != 0:
            raise CronError(f"Could not write the crontab: {result.stderr.strip()}")

    @staticmethod
    def _crontab(*arguments: str, content: str | None = None) -> subprocess.CompletedProcess:
        if shutil.which("crontab") is None:
            raise CronError("The `crontab` command is missing. Install cron (cronie on Fedora and Arch).")
        return subprocess.run(
            ["crontab", *arguments], input=content, capture_output=True, text=True, timeout=10
        )
