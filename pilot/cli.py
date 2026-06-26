import sys

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


def _run_frappe(bench_name: str | None, frappe_args: list[str], verbose: bool = False) -> None:
    from pilot import loader
    from pilot.commands.frappe_cmd import FrappeCommand

    loader.set_active_bench(bench_name)
    try:
        FrappeCommand(loader.load_bench()).run_raw(["frappe", *frappe_args])
    except BenchError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if verbose:
            raise
        print(str(e), file=sys.stderr)
        sys.exit(1)


def main() -> None:
    from pilot import loader, registry

    # Make print() output appear immediately, even before subprocess output.
    sys.stdout.reconfigure(line_buffering=True)
    args_list = sys.argv[1:]

    # Forward unknown commands/options straight to Frappe bench.
    if _is_frappe_passthrough(args_list):
        bench_name, clean = _strip_bench_flag(args_list)
        _run_frappe(bench_name, clean)
        return

    # Early-dispatch 'bench frappe <args...>' before argparse so that sub-options
    # like --site don't get consumed by the top-level parser.
    bench_name, remaining = _strip_bench_flag(args_list)
    if remaining and remaining[0] == "frappe":
        _run_frappe(bench_name, remaining[1:], verbose="--verbose" in args_list)
        return

    # Parse with the bench flag stripped so -b/--bench works after the subcommand too.
    parser = registry.build_parser()
    args = parser.parse_args(remaining)
    if bench_name != "all":
        loader.set_active_bench(bench_name)

    import time

    verbose = getattr(args, "verbose", False)
    _t0 = time.monotonic()
    try:
        if bench_name == "all":
            registry.dispatch_all(args, parser)
        else:
            registry.dispatch(args, parser)
    except BenchError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if verbose:
            raise
        print(str(e), file=sys.stderr)
        sys.exit(1)
    elapsed = time.monotonic() - _t0
    if elapsed >= 2:
        mins, secs = divmod(int(elapsed), 60)
        print(f"\nDone in {mins}m {secs}s" if mins else f"\nDone in {secs}s")
