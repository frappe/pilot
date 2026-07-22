from __future__ import annotations

import argparse
import functools
import importlib
import logging
import pkgutil

from pilot.commands import BenchMode, Command
from pilot.exceptions import BenchError
from pilot.internal.cli.command import add_command_arguments, command_from_args
from pilot.internal.cli.dispatch import CliContext, load_bench

# Help text for command groups (e.g. `bench setup ...`).
GROUP_HELP = {
    "setup": "Production setup commands.",
    "remove": "Teardown commands.",
    "tasks": "Admin task worker controls.",
}


@functools.cache
def _discover() -> list[type[Command]]:
    """Import every module under pilot.commands and collect Command subclasses
    that define a `name`. Cached for the process lifetime."""
    import pilot.commands as pkg

    for mod in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + "."):
        importlib.import_module(mod.name)

    found: dict[tuple[str | None, str], type[Command]] = {}

    def collect(cls: type[Command]) -> None:
        for sub in cls.__subclasses__():
            if getattr(sub, "name", None):
                found[(sub.group, sub.name)] = sub
            collect(sub)

    collect(Command)
    return sorted(found.values(), key=lambda c: (c.group or "", c.name))


def command_names() -> frozenset[str]:
    """Top-level command names (incl. group names) - used to tell bench commands
    from Frappe passthrough."""
    top_level = {c.name for c in _discover() if c.group is None}
    return frozenset(top_level | GROUP_HELP.keys())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench", description="Frappe bench manager")
    parser.add_argument("--verbose", action="store_true", help="Show full tracebacks on error.")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts.")
    parser.add_argument(
        "--bench",
        "-b",
        metavar="NAME",
        default=None,
        help="Bench to operate on (name inside benches/).",
    )

    sub = parser.add_subparsers(dest="command")

    # Create a sub-parser action for each command group up front.
    # `help=` populates the parent listing; `description=` populates the
    # subcommand's own `--help` page - set both so text shows in both places.
    group_subparsers: dict[str, argparse._SubParsersAction] = {}
    for gname, ghelp in GROUP_HELP.items():
        gparser = sub.add_parser(gname, help=ghelp, description=ghelp)
        gparser.set_defaults(_help_printer=gparser.print_help)
        group_subparsers[gname] = gparser.add_subparsers(dest=f"{gname}_command")

    for cls in _discover():
        target = group_subparsers[cls.group] if cls.group else sub
        cmd_parser = target.add_parser(cls.name, help=cls.help, description=cls.help)
        add_command_arguments(cls, cmd_parser)
        cmd_parser.set_defaults(_command_cls=cls)

    return parser


def dispatch(args: argparse.Namespace, parser: argparse.ArgumentParser, context: CliContext) -> None:
    cls: type[Command] | None = getattr(args, "_command_cls", None)
    if cls is None:
        printer = getattr(args, "_help_printer", None)
        (printer or parser.print_help)()
        return
    command_from_args(cls, args, _resolve_bench(cls, context)).run()


def _resolve_bench(cls: type[Command], context: CliContext):
    mode = cls.bench_mode
    if mode is BenchMode.NONE:
        return None
    if mode is BenchMode.OPTIONAL:
        try:
            return load_bench(context)
        except Exception as exc:
            logging.debug("Optional bench load failed: %s", exc)
            return None
    return load_bench(context, require_explicit=mode is BenchMode.EXPLICIT)


def dispatch_all(args: argparse.Namespace, parser: argparse.ArgumentParser, context: CliContext) -> None:
    """Run the command once per production bench (`-b all`); dev benches are skipped
    because their foreground `start` would hang the loop."""
    cls: type[Command] | None = getattr(args, "_command_cls", None)
    if cls is None:
        printer = getattr(args, "_help_printer", None)
        (printer or parser.print_help)()
        return
    if not cls.supports_all_benches:
        raise BenchError(f"'-b all' isn't supported for '{cls.name}'. Pass a specific bench name instead.")

    benches_dir = context.installation_root / "benches"
    names = (
        sorted(d.name for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists())
        if benches_dir.is_dir()
        else []
    )
    if not names:
        raise BenchError("No benches found.")

    failed = []
    for name in names:
        bench = load_bench(context.for_bench(name))
        if not bench.config.production.enabled:
            print(f"== {name} == (skipped: dev mode)")
            continue
        print(f"== {name} ==")
        try:
            command_from_args(cls, args, bench).run()
        except BenchError as e:
            failed.append(name)
            print(str(e))

    if failed:
        raise BenchError(f"Failed for: {', '.join(failed)}")
