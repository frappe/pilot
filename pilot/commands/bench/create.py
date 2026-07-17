from dataclasses import dataclass
from pathlib import Path
from typing import Annotated, ClassVar, Literal

from pilot.commands.base import Arg, BenchMode, Command


@dataclass(kw_only=True)
class NewCommand(Command):
    name: ClassVar[str] = "new"
    help: ClassVar[str] = "Create a new bench."
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    target_directory: Annotated[Path, Arg(cli=False)]
    bench_name: Annotated[str, Arg(help="Name for the new bench.", metavar="name")]
    process_manager: Annotated[str, Arg(cli=False)] = ""
    admin_domain: Annotated[
        str,
        Arg(
            help="Admin domain for this bench. Optional for development; "
            "required by 'bench setup production' (pass it there if omitted here)."
        ),
    ] = ""
    # None -> inherit the server-wide value from a sibling bench (default False).
    admin_tls: Annotated[bool | None, Arg(cli=False)] = None
    database: Annotated[
        Literal["mariadb", "postgres", "sqlite"],
        Arg(help="Database engine for this bench's sites (default: mariadb)."),
    ] = "mariadb"

    @classmethod
    def from_args(cls, args, bench) -> "NewCommand":
        from pilot.loader import cli_root

        return cls(
            target_directory=cli_root() / "benches" / args.bench_name,
            bench_name=args.bench_name,
            admin_domain=args.admin_domain,
            database=args.database,
        )

    def run(self) -> None:
        from pilot.core.bench import Bench

        Bench.create_at(
            self.target_directory,
            self.bench_name,
            process_manager=self.process_manager,
            admin_domain=self.admin_domain,
            admin_tls=self.admin_tls,
            db_type=self.database,
            on_progress=self.print,
        )
