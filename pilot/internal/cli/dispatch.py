from __future__ import annotations

import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import cli_root

if TYPE_CHECKING:
    from pilot.core.bench import Bench

_OWN_GROUP_OPTIONS = frozenset(["--verbose", "--yes", "-y", "--bench", "-b", "--site", "--help", "-h"])
_VALUE_OPTIONS = ("--bench", "-b", "--site")


@dataclass(frozen=True)
class CliContext:
    installation_root: Path
    bench_name: str | None = None
    site_name: str | None = None
    verbose: bool = False
    assume_yes: bool = False

    @property
    def is_all_benches(self) -> bool:
        return self.bench_name == "all"

    def for_bench(self, name: str) -> "CliContext":
        return replace(self, bench_name=name)


def find_bench_root(context: CliContext, require_explicit: bool = False) -> Path:
    benches_dir = context.installation_root / "benches"

    if context.bench_name:
        return _explicit_bench_root(benches_dir, context.bench_name)

    if current_root := _cwd_bench_root():
        return current_root

    if require_explicit:
        raise BenchError(
            "This command needs an explicit bench - run it from inside the bench "
            "directory, or pass -b <name>.\n" + available_hint(benches_dir, sort=True)
        )

    return _implicit_bench_root(benches_dir)


def _explicit_bench_root(benches_dir: Path, bench_name: str) -> Path:
    bench_dir = benches_dir / bench_name
    if not (bench_dir / "bench.toml").exists():
        raise BenchError(f"Bench '{bench_name}' not found.\n{available_hint(benches_dir)}")
    return bench_dir


def _cwd_bench_root() -> Path | None:
    current = Path.cwd()
    for directory in [current, *current.parents]:
        if (directory / "bench.toml").exists():
            return directory
    return None


def _implicit_bench_root(benches_dir: Path) -> Path:
    if benches_dir.is_dir():
        candidates = [d for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = ", ".join(d.name for d in sorted(candidates))
            raise BenchError(f"Multiple benches found: {names}\nSpecify one with: pilot -b <name> <command>")

    raise BenchError("No bench found. Create one with: pilot new <name>")


def load_bench(context: CliContext, require_explicit: bool = False) -> "Bench":
    from pilot.core.bench import Bench

    bench_root = find_bench_root(context, require_explicit=require_explicit)
    return Bench(bench_root)


def strip_value_flag(args: list[str], *aliases: str) -> tuple[str | None, list[str]]:
    """Pull `--name value` or `--name=value` out of args wherever it sits, so a flag typed
    after the command still reaches pilot instead of argparse or the frappe passthrough."""
    value = None
    remaining = []
    skip_next = False
    for arg in args:
        if skip_next:
            value = arg
            skip_next = False
            continue
        if arg in aliases:
            skip_next = True
            continue
        if any(arg.startswith(f"{alias}=") for alias in aliases):
            value = arg.split("=", 1)[1]
            continue
        remaining.append(arg)
    return value, remaining


def is_frappe_passthrough(args: list[str], own_commands: frozenset[str] | None = None) -> bool:
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
                skip_next = arg in _VALUE_OPTIONS
                continue
            return True
        return arg not in own_commands
    return False


def is_own_group_option(arg: str) -> bool:
    if arg in _OWN_GROUP_OPTIONS:
        return True
    if "=" not in arg:
        return False
    key = arg.split("=", 1)[0]
    return key in _VALUE_OPTIONS


def main() -> None:
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    args_list = sys.argv[1:]
    bench_name, remaining = strip_value_flag(args_list, "--bench", "-b")
    site_name, remaining = strip_value_flag(remaining, "--site")
    context = build_context(
        bench_name,
        site_name,
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


def build_context(
    bench_name: str | None, site_name: str | None, verbose: bool, assume_yes: bool
) -> CliContext:
    return CliContext(
        installation_root=cli_root(),
        bench_name=bench_name,
        site_name=site_name,
        verbose=verbose,
        assume_yes=assume_yes,
    )


def forwarded_frappe_args(remaining: list[str]) -> list[str] | None:
    if remaining and remaining[0] == "frappe":
        return remaining[1:]
    if is_frappe_passthrough(remaining):
        return remaining
    return None


def run_frappe(context: CliContext, args: list[str]) -> None:
    from pilot.commands.runtime.frappe import FrappeCommand

    with error_boundary(context):
        bench = load_bench(context)
        FrappeCommand(bench, args=tuple(frappe_args(bench, context, args))).run()


def frappe_args(bench: "Bench", context: CliContext, args: list[str]) -> list[str]:
    """Frappe wants --site ahead of its own command, so put the resolved site there."""
    if context.site_name:
        return ["--site", context.site_name, *args]
    try:
        return ["--site", bench.default_site.config.name, *args]
    except BenchError:
        # No site pilot can name on its own - leave the choice to frappe.
        return args


def run_native(context: CliContext, remaining: list[str]) -> None:
    import time

    from pilot.internal.cli import registry

    parser = registry.build_parser()
    args = parser.parse_args(remaining)
    started = time.monotonic()
    with error_boundary(context):
        if context.is_all_benches:
            registry.dispatch_all(args, parser, context)
        else:
            registry.dispatch(args, parser, context)
    report_elapsed(time.monotonic() - started)


def report_elapsed(elapsed: float) -> None:
    if elapsed >= 2:
        minutes, seconds = divmod(int(elapsed), 60)
        print(f"\nDone in {minutes}m {seconds}s" if minutes else f"\nDone in {seconds}s")


def available_hint(benches_dir: Path, sort: bool = False) -> str:
    if not benches_dir.is_dir():
        return "  No benches found. Run: pilot new <name>"
    names = [d.name for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()]
    if not names:
        return "  No benches found. Run: pilot new <name>"
    return f"  Available: {', '.join(sorted(names) if sort else names)}"
