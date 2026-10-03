from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.site import Site


@dataclass(kw_only=True)
class RecoverCommand(Command):
    name: ClassVar[str] = "recover"
    help: ClassVar[str] = "Disaster Recovery: pull offsite S3 backups and restore bench sites."
    bench_mode: ClassVar[BenchMode] = BenchMode.AUTO
    supports_all_benches: ClassVar[bool] = True

    site: Annotated[
        str | None,
        Arg(
            help="Specific site name to recover (defaults to all sites in bench).",
            short="s",
            metavar="SITE",
        ),
    ] = None
    timestamp: Annotated[
        str | None,
        Arg(
            help="Specific backup timestamp (YYYYMMDD_HHMMSS) to restore (defaults to latest).",
            short="t",
            metavar="TIMESTAMP",
        ),
    ] = None
    dry_run: Annotated[
        bool,
        Arg(help="List available offsite backups from S3 without restoring."),
    ] = False
    leave_maintenance: Annotated[
        bool,
        Arg(help="Keep site in maintenance mode after recovery completes."),
    ] = False

    def run(self) -> None:
        bench_label = self.bench.config.name or self.bench.path.name

        if not self.bench.config.s3.is_configured:
            raise BenchError(
                f"S3 offsite backups are not configured in bench.toml for '{bench_label}'."
            )

        sites = [self.bench.site(self.site)] if self.site else self.bench.sites()
        if not sites:
            raise BenchError(
                f"No sites found in bench '{bench_label}'."
                if not self.site
                else f"Site '{self.site}' does not exist in bench '{bench_label}'."
            )

        if self.dry_run:
            self._run_dry_run(sites, bench_label)
            return

        self._run_recovery(sites, bench_label)

    def _run_dry_run(self, sites: list[Site], bench_label: str) -> None:
        from pilot.core.site.recovery import SiteRecovery

        self.report(f"🔍 Inspecting offsite S3 backups for bench '{bench_label}'...\n")
        for site in sites:
            self.report(f"Site: {site.config.name}")
            recovery = SiteRecovery(site)
            try:
                backups = recovery.list_available_backups(limit=5)
                if not backups:
                    self.report("  (No offsite backups found in S3)")
                    continue
                for ts, files in backups.items():
                    db = files.get("database", "no-db")
                    extras = []
                    if "files" in files:
                        extras.append("files")
                    if "private_files" in files:
                        extras.append("private_files")
                    if "site_config" in files:
                        extras.append("site_config")
                    extras_str = f" [{', '.join(extras)}]" if extras else ""
                    self.report(f"  • {ts} ➔ {db}{extras_str}")
            except Exception as exc:
                self.report(f"  Error checking S3: {exc}")
            self.report("")

    def _run_recovery(self, sites: list[Site], bench_label: str) -> None:
        from pilot.core.site.recovery import SiteRecovery

        successful: list[tuple[str, str]] = []
        failed: list[tuple[str, str]] = []
        self.report(f"🚀 Starting Disaster Recovery for bench '{bench_label}'...")

        for site in sites:
            site_name = site.config.name
            self.report(f"\n--- Recovering {site_name} ---")
            recovery = SiteRecovery(site)
            try:
                restored_ts = recovery.recover(
                    timestamp=self.timestamp,
                    leave_maintenance=self.leave_maintenance,
                    on_progress=self.report,
                )
                successful.append((site_name, restored_ts))
                self.report(f"✅ Successfully recovered site '{site_name}' (backup: {restored_ts}).")
            except Exception as exc:
                failed.append((site_name, str(exc)))
                self.report(f"❌ Failed to recover site '{site_name}': {exc}")

        self._report_recovery_summary(successful, failed, bench_label)

    def _report_recovery_summary(
        self,
        successful: list[tuple[str, str]],
        failed: list[tuple[str, str]],
        bench_label: str,
    ) -> None:
        self.report("\n==================== Recovery Summary ====================")
        for s_name, ts in successful:
            self.report(f"  ✅ {s_name}: Restored snapshot {ts}")
        for f_name, err in failed:
            self.report(f"  ❌ {f_name}: {err}")

        if failed:
            raise BenchError(f"Disaster Recovery failed for: {', '.join(f[0] for f in failed)}")
        self.report(f"\n🎉 Disaster Recovery completed successfully for bench '{bench_label}'!")
