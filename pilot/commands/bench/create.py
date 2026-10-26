from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pilot.commands import Arg, BenchMode, Command


@dataclass(kw_only=True)
class NewCommand(Command):
    name: ClassVar[str] = "new"
    help: ClassVar[str] = "Create a new bench."
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    bench_name: Annotated[str, Arg(help="Name for the new bench.", metavar="name")]
    frappe_branch: Annotated[
        str | None, Arg(help="Frappe branch to use (default: version-16).")
    ] = None
    target_directory: Annotated[Path | None, Arg(cli=False)] = None
    process_manager: Annotated[str, Arg(cli=False)] = ""
    admin_domain: Annotated[
        str,
        Arg(
            help="Admin domain for this bench. Optional for development; "
            "required by 'pilot setup production' (pass it there if omitted here)."
        ),
    ] = ""
    # None -> inherit the server-wide value from a sibling bench (default False).
    admin_tls: Annotated[bool | None, Arg(cli=False)] = None
    database: Annotated[
        Literal["mariadb", "postgres", "sqlite"],
        Arg(help="Database engine for this bench's sites (default: mariadb)."),
    ] = "mariadb"
    admin_password: Annotated[
        str | None,
        Arg(help="Admin panel password. Prompted for on a terminal, else generated."),
    ] = None

    def __post_init__(self) -> None:
        if self.target_directory is not None:
            return
        from pilot.utils import cli_root

        self.target_directory = cli_root() / "benches" / self.bench_name

    def run(self) -> None:
        from pilot.core.bench import Bench

        Bench.create_at(
            self.target_directory,
            self.bench_name,
            frappe_branch=self.frappe_branch,
            process_manager=self.process_manager,
            admin_domain=self.admin_domain,
            admin_tls=self.admin_tls,
            db_type=self.database,
            admin_password=self.resolve_password(self.admin_password),
            on_progress=self.report,
        )
