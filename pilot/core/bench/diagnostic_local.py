from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.bench.diagnostics import DiagnosticReport

MIB = 1024**2
MINIMUM_FREE_BYTES = 500 * MIB
LOW_SPACE_CEILING = 5 * 1024**3
MINIMUM_FREE_FRACTION = 0.05


class LocalDiagnostics:
    """Read-only capacity measurements and effective site maintenance settings."""

    def __init__(self, report: DiagnosticReport) -> None:
        self.report = report

    def check_capacity(self) -> None:
        try:
            stats = os.statvfs(self.report.path)
        except OSError:
            for code in ("disk_capacity", "inode_capacity"):
                self.add(
                    code,
                    "unavailable",
                    "bench",
                    self.report.path,
                    "filesystem_unavailable",
                    "Cannot measure the bench filesystem.",
                    "Check path access and filesystem availability.",
                )
            return
        unit = stats.f_frsize or stats.f_bsize
        available, total = stats.f_bavail * unit, stats.f_blocks * unit
        low = available < MINIMUM_FREE_BYTES or (
            available < LOW_SPACE_CEILING and available < total * MINIMUM_FREE_FRACTION
        )
        self.add(
            "disk_capacity",
            "error" if low else "ok",
            "bench",
            self.report.path,
            "low_disk_space" if low else "",
            "Bench filesystem user-available disk space measured.",
            "Free space or expand the bench filesystem before writes, backups, or migrations." if low else "",
            {
                "available_bytes": available,
                "total_bytes": total,
                "minimum_free_bytes": MINIMUM_FREE_BYTES,
                "low_space_ceiling_bytes": LOW_SPACE_CEILING,
                "minimum_free_fraction": MINIMUM_FREE_FRACTION,
            },
        )
        self.check_inodes(stats)

    def check_inodes(self, stats) -> None:
        if stats.f_files <= 0:
            self.add(
                "inode_capacity",
                "skipped",
                "bench",
                self.report.path,
                "inode_counts_unavailable",
                "This filesystem does not expose inode capacity.",
                "",
            )
            return
        low = stats.f_favail < stats.f_files * MINIMUM_FREE_FRACTION
        self.add(
            "inode_capacity",
            "error" if low else "ok",
            "bench",
            self.report.path,
            "low_inodes" if low else "",
            "Bench filesystem user-available inodes measured.",
            "Review and remove obsolete small files or expand the filesystem; avoid deleting site data."
            if low
            else "",
            {
                "available_inodes": stats.f_favail,
                "total_inodes": stats.f_files,
                "minimum_free_fraction": MINIMUM_FREE_FRACTION,
            },
        )

    def check_maintenance(self, site: str, settings: dict) -> None:
        for key, code in (("maintenance_mode", "maintenance_mode"), ("pause_scheduler", "scheduler_paused")):
            value = settings.get(key, 0)
            if value not in (None, False, True, 0, 1, "0", "1"):
                self.add(
                    code,
                    "error",
                    "site",
                    site,
                    "configuration_invalid",
                    f"{key} must be a boolean or 0/1.",
                    "Repair this setting in the common or site configuration.",
                )
                continue
            enabled = value in (True, 1, "1")
            self.add(
                code,
                "error" if enabled else "ok",
                "site",
                site,
                key + "_enabled" if enabled else "",
                f"Effective {key} is {'enabled' if enabled else 'disabled'}.",
                "Confirm migrations and restores have finished before clearing this site's maintenance settings."
                if enabled
                else "",
                {key: enabled},
            )

    def add(self, code, status, scope, target, reason, message, fix, details=None) -> None:
        from pilot.core.bench.diagnostics import DiagnosticCheck

        self.report.checks.append(
            DiagnosticCheck(
                code=code,
                status=status,
                path=self.report.path,
                message=message,
                fix=fix,
                scope=scope,
                target=target,
                reason=reason,
                details=details or {},
            )
        )
