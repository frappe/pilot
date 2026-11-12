from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, ClassVar

from pilot.commands import Arg, BenchMode, Command
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


@dataclass(kw_only=True)
class DropSiteCommand(Command):
    name: ClassVar[str] = "drop-site"
    help: ClassVar[str] = "Drop a site, and remove it from bench.toml, nginx and the backup schedule."
    bench_mode: ClassVar[BenchMode] = BenchMode.EXPLICIT

    site_name: Annotated[str, Arg(help="Site name.", metavar="site")]
    no_backup: Annotated[bool, Arg(help="Drop without the backup Frappe takes first.")] = False
    force: Annotated[bool, Arg(help="Drop even when Frappe hits an error, such as a failed backup.")] = False
    archived_sites_path: Annotated[str | None, Arg(help="Folder that receives the archived site.")] = None
    skip_confirm: bool = False

    def run(self) -> None:
        site = self.bench.site(self.site_name)
        site.ensure_exists()
        with site.reserve():
            self.confirm(f"Permanently delete site '{self.site_name}'?", skip=self.skip_confirm)
            site.drop(
                on_progress=self.report,
                no_backup=self.no_backup,
                force=self.force,
                archived_sites_path=self.archived_sites_path,
            )

    @classmethod
    def from_frappe_args(cls, bench: "Bench", args: tuple[str, ...]) -> "DropSiteCommand":
        """Parse Frappe's `[--site SITE] drop-site SITE [options]` form. The bench's own
        database credentials are always used."""
        parser = argparse.ArgumentParser(prog="drop-site", add_help=False, exit_on_error=False)
        parser.add_argument("--site")
        parser.add_argument("command")
        parser.add_argument("site_name", nargs="?")
        parser.add_argument("--no-backup", action="store_true")
        parser.add_argument("--force", action="store_true")
        parser.add_argument("--archived-sites-path")
        for names in (
            ("--db-root-username", "--mariadb-root-username", "--root-login"),
            ("--db-root-password", "--mariadb-root-password", "--root-password"),
        ):
            parser.add_argument(*names, dest=names[0].lstrip("-").replace("-", "_"))
        try:
            parsed = parser.parse_args(args)
        except (argparse.ArgumentError, SystemExit) as error:
            raise BenchError(f"Could not read the drop-site arguments: {error}") from error
        site_name = parsed.site_name or parsed.site
        if parsed.command != "drop-site" or not site_name:
            raise BenchError("Usage: pilot drop-site SITE [--no-backup] [--force] [--archived-sites-path PATH]")
        command = cls(
            bench,
            site_name=site_name,
            no_backup=parsed.no_backup,
            force=parsed.force,
            archived_sites_path=parsed.archived_sites_path,
            skip_confirm=True,
        )
        if parsed.db_root_username or parsed.db_root_password:
            command.report("Pilot drops the site with the bench's own database credentials.")
        return command
