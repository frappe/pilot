from __future__ import annotations

import shlex
import subprocess
import sys
from pathlib import Path

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

    def _read_crontab(self) -> list[str]:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return []
        return result.stdout.splitlines()

    def _write_crontab(self, lines: list[str]) -> None:
        non_empty = [line for line in lines if line.strip()]
        if not non_empty:
            subprocess.run(["crontab", "-r"], capture_output=True, timeout=10)
            return
        content = "\n".join(non_empty) + "\n"
        proc = subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True, timeout=10)
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to write crontab: {proc.stderr}")
