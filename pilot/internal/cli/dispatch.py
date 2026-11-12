from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from itertools import dropwhile
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_OWN_GROUP_OPTIONS = frozenset(["--verbose", "--yes", "-y", "--bench", "-b", "--help", "-h", "--version"])
# Pilot flags given before a Frappe command; Frappe commands may take their own --yes after it.
_PILOT_ONLY_FLAGS = frozenset(["--verbose", "--yes", "-y"])


@dataclass(frozen=True)
class CliContext:
    installation_root: Path
    bench_name: str | None = None
    verbose: bool = False
    assume_yes: bool = False

    @property
    def is_all_benches(self) -> bool:
        """Whether the command targets every bench."""
        return self.bench_name == "all"

    def for_bench(self, name: str) -> "CliContext":
        """A copy of the context for one named bench."""
        return replace(self, bench_name=name)


def find_bench_root(context: CliContext, require_explicit: bool = False) -> Path:
    """The bench directory from --bench, the current directory, or the only bench."""
    benches_dir = context.installation_root / "benches"

    if context.bench_name:
        return _explicit_bench_root(benches_dir, context.bench_name)

    if current_root := _cwd_bench_root() or _cwd_workspace_bench_root():
        return current_root

    if require_explicit:
        raise BenchError(
            "This command needs an explicit bench - run it from inside the bench "
            "directory, or pass -b <name>.\n" + available_hint(benches_dir, sort=True)
        )

    return _implicit_bench_root(benches_dir)


def _explicit_bench_root(benches_dir: Path, bench_name: str) -> Path:
    """The named bench's directory; raise with the available names when it is missing."""
    bench_dir = benches_dir / bench_name
    if not (bench_dir / "bench.toml").exists():
        raise BenchError(f"Bench '{bench_name}' not found.\n{available_hint(benches_dir)}")
    return bench_dir


def _cwd_bench_root() -> Path | None:
    """The nearest bench directory above the current directory, if any."""
    current = Path.cwd()
    for directory in [current, *current.parents]:
        if (directory / "bench.toml").exists():
            return directory
    return None


def _cwd_workspace_bench_root() -> Path | None:
    """The bench linked to the app repository around the current directory."""
    from pilot.core.workspace import Workspace

    workspace = Workspace.find()
    bench = workspace.find_bench() if workspace else None
    return bench.path if bench else None


def _implicit_bench_root(benches_dir: Path) -> Path:
    """The only bench on the host; raise when there are none or several."""
    if benches_dir.is_dir():
        candidates = [d for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = ", ".join(d.name for d in sorted(candidates))
            raise BenchError(f"Multiple benches found: {names}\nSpecify one with: pilot -b <name> <command>")

    raise BenchError("No bench found. Create one with: pilot new <name>")


def load_bench(context: CliContext, require_explicit: bool = False) -> "Bench":
    """The bench the CLI context points at."""
    from pilot.core.bench import Bench

    bench_root = find_bench_root(context, require_explicit=require_explicit)
    return Bench(bench_root)


def strip_bench_flag(args: list[str]) -> tuple[str | None, list[str]]:
    """The --bench value and the remaining arguments without it."""
    bench_name = None
    remaining = []
    skip_next = False
    for arg in args:
        if skip_next:
            bench_name = arg
            skip_next = False
            continue
        if arg in ("--bench", "-b"):
            skip_next = True
            continue
        if arg.startswith(("--bench=", "-b=")):
            bench_name = arg.split("=", 1)[1]
            continue
        remaining.append(arg)
    return bench_name, remaining


def is_frappe_passthrough(args: list[str], own_commands: frozenset[str] | None = None) -> bool:
    """Whether the arguments are a Frappe bench command rather than a Pilot command."""
    if own_commands is None:
        from pilot.internal.cli.registry import command_names

        own_commands = command_names()

    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            if is_own_group_option(arg):
                skip_next = arg in ("--bench", "-b")
                continue
            return True
        return arg not in own_commands
    return False


def is_own_group_option(arg: str) -> bool:
    """Whether the argument is an option Pilot handles itself, with or without a value."""
    if arg in _OWN_GROUP_OPTIONS:
        return True
    if "=" not in arg:
        return False
    key = arg.split("=", 1)[0]
    return key in ("--bench", "-b")


def main() -> None:
    """The pilot command entry point."""
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    args_list = sys.argv[1:]
    bench_name, remaining = strip_bench_flag(args_list)
    context = build_context(
        bench_name,
        verbose="--verbose" in args_list,
        assume_yes="--yes" in args_list or "-y" in args_list,
    )

    forwarded_args = forwarded_frappe_args(remaining)
    if forwarded_args is not None:
        run_frappe(context, forwarded_args)
    else:
        run_native(context, remaining)


@contextmanager
def error_boundary(context: CliContext) -> Iterator[None]:
    """Turn errors and Ctrl-C into short messages and exit codes."""
    try:
        yield
    except KeyboardInterrupt:
        print("\n\033[31mAborted by user.\033[0m", file=sys.stderr)
        sys.exit(130)
    except BenchError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
    except Exception as error:
        if context.verbose:
            raise
        print(str(error), file=sys.stderr)
        sys.exit(1)


def build_context(bench_name: str | None, verbose: bool, assume_yes: bool) -> CliContext:
    """The CLI context for this invocation."""
    from pilot.core.server import Server

    return CliContext(
        installation_root=Server().root,
        bench_name=bench_name,
        verbose=verbose,
        assume_yes=assume_yes,
    )


def forwarded_frappe_args(remaining: list[str]) -> list[str] | None:
    """The arguments to pass to Frappe's bench, or None when Pilot handles the command."""
    if remaining in (["frappe", "--help"], ["frappe", "-h"]):
        return None
    if remaining and remaining[0] == "frappe":
        return remaining[1:]
    if is_frappe_passthrough(remaining):
        return list(dropwhile(lambda arg: arg in _PILOT_ONLY_FLAGS, remaining))
    return None


def run_frappe(context: CliContext, args: list[str]) -> None:
    """Run a Frappe bench command in the selected bench."""
    from pilot.commands.runtime.frappe import FrappeCommand

    with error_boundary(context):
        FrappeCommand(load_bench(context), args=tuple(args)).run()


def requested_command(remaining: list[str]) -> str | None:
    """The command the user asked for, so the parser can build just that one.

    None when the invocation needs the whole parser: no command, a help or
    version request, or a flag before the command that the CLI does not own.
    """
    for arg in remaining:
        if arg in ("--help", "-h", "--version"):
            return None
        if arg.startswith("-"):
            continue
        return arg
    return None


def run_native(context: CliContext, remaining: list[str]) -> None:
    """Parse and run a Pilot command, then report the time it took."""
    import time

    from pilot.internal.cli import registry

    parser = registry.build_parser(only=requested_command(remaining))
    args = parser.parse_args(remaining)
    started = time.monotonic()
    with error_boundary(context):
        if context.is_all_benches:
            registry.dispatch_all(args, parser, context)
        else:
            registry.dispatch(args, parser, context)
    report_elapsed(time.monotonic() - started)


def report_elapsed(elapsed: float) -> None:
    """Print the elapsed time for commands that took two seconds or more."""
    if elapsed >= 2:
        minutes, seconds = divmod(int(elapsed), 60)
        print(f"\nDone in {minutes}m {seconds}s" if minutes else f"\nDone in {seconds}s")


def available_hint(benches_dir: Path, sort: bool = False) -> str:
    """A hint that lists the benches on the host."""
    if not benches_dir.is_dir():
        return "  No benches found. Run: pilot new <name>"
    names = [d.name for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()]
    if not names:
        return "  No benches found. Run: pilot new <name>"
    return f"  Available: {', '.join(sorted(names) if sort else names)}"
