from __future__ import annotations

import subprocess
from pathlib import Path

_MARKER_PREFIX = "# bench-backup:"


class CronManager:
    def __init__(self, bench_root: Path) -> None:
        self._bench_root = bench_root

    def get_schedule(self, site: str) -> str | None:
        lines = self._read_crontab()
        try:
            i = lines.index(self._marker(site))
            parts = lines[i + 1].split()
            return " ".join(parts[:5]) if len(parts) >= 5 else None
        except (ValueError, IndexError):
            return None

    def set_schedule(self, site: str, cron_expr: str) -> None:
        lines = self._read_crontab()
        marker = self._marker(site)
        command = self._build_command(site)
        try:
            i = lines.index(marker)
            lines[i + 1] = f"{cron_expr} {command}"
        except ValueError:
            lines += [marker, f"{cron_expr} {command}"]
        self._write_crontab(lines)

    def remove_schedule(self, site: str) -> None:
        lines = self._read_crontab()
        marker = self._marker(site)
        try:
            i = lines.index(marker)
            del lines[i : i + 2]
        except ValueError:
            pass
        self._write_crontab(lines)

    def _marker(self, site: str) -> str:
        return f"{_MARKER_PREFIX}{self._bench_root}:{site}"

    def _build_command(self, site: str) -> str:
        python = self._bench_root / "env" / "bin" / "python"
        sites_dir = self._bench_root / "sites"
        log_file = self._bench_root / "logs" / f"backup-{site}.log"
        return f"cd {sites_dir} && {python} -m frappe.utils.bench_helper frappe --site {site} backup --with-files >> {log_file} 2>&1"

    def _read_crontab(self) -> list[str]:
        result = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
        if result.returncode != 0:
            return []
        return result.stdout.splitlines()

    def _write_crontab(self, lines: list[str]) -> None:
        non_empty = [line for line in lines if line.strip()]
        if not non_empty:
            subprocess.run(["crontab", "-r"], capture_output=True)
            return
        content = "\n".join(non_empty) + "\n"
        proc = subprocess.run(["crontab", "-"], input=content, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(f"Failed to write crontab: {proc.stderr}")
