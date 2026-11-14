from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.server.legacy_bench import DatabaseLogin, ImportResult, LegacyApp, LegacyBenchImporter


@dataclass(kw_only=True)
class ImportLegacyBenchCommand(Command):
    name: ClassVar[str] = "import-legacy-bench"
    help: ClassVar[str] = (
        "Move a bench made by the bench CLI into Pilot. Shows the plan and asks before changing anything."
    )
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    path: Annotated[Path, Arg(help="Path of the bench to import.", metavar="path")]
    bench_name: Annotated[
        str, Arg(help="Name for the new bench (default: the folder name). A taken name gets a -N suffix.")
    ] = ""
    dry_run: Annotated[bool, Arg(help="Show the plan without changing anything.")] = False
    skip_confirm: bool = False

    def run(self) -> None:
        from pilot.core.server import Server
        from pilot.core.server.legacy_bench import LegacyImportError

        importer = Server().get_legacy_bench_importer(self.path, self.bench_name)
        self.report_plan(importer)
        importer.validate_sites()
        if self.dry_run:
            return
        self.confirm("Continue?", skip=self.skip_confirm)
        if importer.needs_database_login:
            importer.save_database_login(self.ask_database_login(importer))
        try:
            result = importer.run(self.report)
        except LegacyImportError as error:
            self.report(f"\nImport failed: {error}")
            self.offer_revert(importer)
            raise
        self.report_result(importer, result)
        if result.has_failures:
            self.offer_revert(importer)
            raise BenchError("Some sites were not imported. See the errors above.")

    def ask_database_login(self, importer: "LegacyBenchImporter") -> "DatabaseLogin":
        from getpass import getpass

        from pilot.core.server.legacy_bench import DatabaseLogin

        server = importer.pilot_server
        if not sys.stdin.isatty():
            raise BenchError(
                f"Pilot has no database server yet. Run this in a terminal to log in to {server.label}."
            )
        self.report(
            f"Pilot has no database server yet. Log in to {server.label}; Pilot will share it with its benches."
        )
        user = input("Root user [root]: ").strip() or "root"
        return DatabaseLogin(server, user, getpass("Root password: "))

    def offer_revert(self, importer: "LegacyBenchImporter") -> None:
        """Revert on `pilot --yes` or a yes on a terminal; otherwise keep the bench as it is."""
        if self.skip_confirm or (sys.stdin.isatty() and self.ask_revert(importer)):
            importer.revert(self.report)
            return
        self.report(f"\nKept the bench at {importer.bench_path} so you can look into it.")
        self.report(f"The old host files and site folders are in {importer.bench_path / '.legacy'}.")

    def ask_revert(self, importer: "LegacyBenchImporter") -> bool:
        prompt = (
            f"Revert? The folder moves back to {importer.legacy.path}. Copied databases are dropped; "
            "databases used in place stay migrated."
        )
        try:
            return input(f"{prompt} [y/N] ").strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return False

    def report_plan(self, importer: "LegacyBenchImporter") -> None:
        legacy = importer.legacy
        self.report(f"Plan: move {legacy.path} to {importer.bench_path} as bench '{importer.name}'.")
        self.report("Stop the old bench first. Its env, Procfile, config and site folders go to .legacy/.\n")
        self.report("Pilot supports Frappe v16 and Nightly only.")
        self.report(
            "Make sure the old bench's frappe is on v16 or Nightly, or a branch based on one of them."
        )
        self.report(f"  frappe: {legacy.framework_app.revision}\n")
        self.report("Apps:")
        for app in legacy.apps:
            self.report(f"  {app.name}: {app.revision}{get_app_notes(app)}")
        if legacy.skipped_apps:
            self.report(
                f"Set aside in .legacy/apps, used by neither the bench nor a site: {', '.join(legacy.skipped_apps)}"
            )
        if legacy.missing_apps:
            self.report(
                f"Missing from apps/, so sites using them will fail: {', '.join(legacy.missing_apps)}"
            )
        self.report_database_plan(importer)

    def report_database_plan(self, importer: "LegacyBenchImporter") -> None:
        server = importer.pilot_server.label
        if importer.needs_database_login:
            self.report(
                f"\nDatabase: Pilot has none yet. You will log in to {server}, and Pilot will use it."
            )
        else:
            self.report(f"\nDatabase: Pilot's {importer.engine} server is {server}.")
        self.report("Sites:")
        for plan in importer.site_plans:
            database = plan.legacy_site.config.get("db_name", "")
            how = (
                f"keeps database {database}"
                if plan.keeps_database
                else f"database {database} is copied to {server}"
            )
            self.report(f"  {plan.legacy_site.name} -> {plan.name}  ({how})")
        if not importer.site_plans:
            self.report("  (none)")
        self.report("")

    def report_result(self, importer: "LegacyBenchImporter", result: "ImportResult") -> None:
        bench = result.bench
        self.report(f"\nMoved {result.legacy_path} -> bench '{bench.config.name}' ({bench.path})")
        for site in result.sites:
            status = f"failed: {site.error}" if site.error else "ok"
            self.report(f"  Site {site.legacy_name} -> {site.name} [{status}]")
        self.report(f"  Admin: http://localhost:{bench.config.admin.port}  password: {result.admin_password}")
        self.report(f"  Next:  pilot -b {bench.config.name} start")
        self.report(f"\nOnce the bench works, delete {bench.path / '.legacy'}.")
        for plan in importer.site_plans:
            if not plan.keeps_database:
                database = plan.legacy_site.config.get("db_name", "")
                self.report(
                    f"{plan.legacy_site.name}'s old database {database} on {plan.legacy_site.database_server.label} "
                    "is no longer used; drop it when you no longer need it."
                )


def get_app_notes(app: "LegacyApp") -> str:
    notes = []
    if app.has_local_changes:
        notes.append("uncommitted changes")
    if app.has_credentials_in_remote:
        notes.append("token kept out of bench.toml")
    return f"  ({'; '.join(notes)})" if notes else ""
