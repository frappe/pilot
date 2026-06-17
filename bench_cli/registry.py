from __future__ import annotations

import argparse
import importlib
import pkgutil

from bench_cli.commands.base import Command
from bench_cli.loader import load_bench

# Help text for command groups (e.g. `bench setup ...`, `bench volume ...`).
GROUP_HELP = {
    "setup": "Production setup commands.",
    "volume": "ZFS volume management commands.",
}

_commands_cache: list[type[Command]] | None = None


def _discover() -> list[type[Command]]:
    """Import every module under bench_cli.commands and collect Command subclasses
    that define a `name`. Result is memoised for the process lifetime."""
    global _commands_cache
    if _commands_cache is not None:
        return _commands_cache

    import bench_cli.commands as pkg

    for mod in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        importlib.import_module(mod.name)

    found: dict[tuple[str | None, str], type[Command]] = {}

    def collect(cls: type[Command]) -> None:
        for sub in cls.__subclasses__():
            if getattr(sub, "name", None):
                found[(sub.group, sub.name)] = sub
            collect(sub)

    collect(Command)
    _commands_cache = sorted(found.values(), key=lambda c: (c.group or "", c.name))
    return _commands_cache


def command_names() -> frozenset[str]:
    """Top-level command names (incl. group names) — used to tell bench commands
    from Frappe passthrough."""
    top_level = {c.name for c in _discover() if c.group is None}
    return frozenset(top_level | GROUP_HELP.keys())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench", description="Frappe bench manager")
    parser.add_argument("--verbose", action="store_true", help="Show full tracebacks on error.")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts.")
    parser.add_argument("--bench", "-b", metavar="NAME", default=None, help="Bench to operate on (name inside benches/).")

    sub = parser.add_subparsers(dest="command")

    # Create a sub-parser action for each command group up front.
    # `help=` populates the parent listing; `description=` populates the
    # subcommand's own `--help` page — set both so text shows in both places.
    group_subparsers: dict[str, argparse._SubParsersAction] = {}
    for gname, ghelp in GROUP_HELP.items():
        gparser = sub.add_parser(gname, help=ghelp, description=ghelp)
        gparser.set_defaults(_help_printer=gparser.print_help)
        group_subparsers[gname] = gparser.add_subparsers(dest=f"{gname}_command")

    for cls in _discover():
        target = group_subparsers[cls.group] if cls.group else sub
        cmd_parser = target.add_parser(cls.name, help=cls.help, description=cls.help)
        cls.add_arguments(cmd_parser)
        cmd_parser.set_defaults(_command_cls=cls)

    return parser


def dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser) -> None:
    cls: type[Command] | None = getattr(args, "_command_cls", None)
    if cls is None:
        # No command, or a group selected without a subcommand.
        printer = getattr(args, "_help_printer", None)
        (printer or parser.print_help)()
        return

    bench = load_bench() if cls.requires_bench else None
    cls.from_args(args, bench).run()
