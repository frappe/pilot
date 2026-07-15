import sys

from pilot.context import CliContext
from pilot.exceptions import BenchError

_OWN_GROUP_OPTIONS = frozenset(["--verbose", "--yes", "-y", "--bench", "-b", "--help", "-h"])


def _strip_bench_flag(args: list[str]) -> tuple[str | None, list[str]]:
    """Strip -b/--bench <name> and return (bench_name, remaining_args)."""
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


def _is_frappe_passthrough(args: list[str], own_commands: frozenset[str] | None = None) -> bool:
    """Forward to Frappe bench when the first meaningful token isn't ours."""
    if own_commands is None:
        from pilot.registry import command_names

        own_commands = command_names()

    skip_next = False
    for arg in args:
        if skip_next:
            skip_next = False
            continue
        if arg.startswith("-"):
            if arg in _OWN_GROUP_OPTIONS:
                # -b/--bench consume the next token (the bench name)
                if arg in ("--bench", "-b"):
                    skip_next = True
                continue
            if "=" in arg:
                # --bench=name form — value is inline, nothing to skip
                key = arg.split("=", 1)[0]
                if key in ("--bench", "-b"):
                    continue
            return True  # unknown option → Frappe passthrough
        return arg not in own_commands
    return False


def _run_frappe(context: CliContext, frappe_args: list[str]) -> None:
    from pilot import loader
    from pilot.commands.frappe_cmd import FrappeCommand

    try:
        FrappeCommand(loader.load_bench(context)).run_raw(["frappe", *frappe_args])
    except BenchError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if context.verbose:
            raise
        print(str(e), file=sys.stderr)
        sys.exit(1)


def _build_context(bench_name: str | None, verbose: bool, assume_yes: bool) -> CliContext:
    from pilot.loader import cli_root

    return CliContext(
        installation_root=cli_root(),
        bench_name=bench_name,
        verbose=verbose,
        assume_yes=assume_yes,
    )


def main() -> None:
    from pilot import registry

    sys.stdout.reconfigure(line_buffering=True)
    args_list = sys.argv[1:]
    bench_name, remaining = _strip_bench_flag(args_list)
    context = _build_context(
        bench_name,
        verbose="--verbose" in args_list,
        assume_yes="--yes" in args_list or "-y" in args_list,
    )

    if _is_frappe_passthrough(args_list):
        _run_frappe(context, remaining)
        return

    if remaining and remaining[0] == "frappe":
        _run_frappe(context, remaining[1:])
        return

    parser = registry.build_parser()
    args = parser.parse_args(remaining)

    import time

    _t0 = time.monotonic()
    try:
        if context.all_benches:
            registry.dispatch_all(args, parser, context)
        else:
            registry.dispatch(args, parser, context)
    except BenchError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if context.verbose:
            raise
        print(str(e), file=sys.stderr)
        sys.exit(1)
    elapsed = time.monotonic() - _t0
    if elapsed >= 2:
        mins, secs = divmod(int(elapsed), 60)
        print(f"\nDone in {mins}m {secs}s" if mins else f"\nDone in {secs}s")
