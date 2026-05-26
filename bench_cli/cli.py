import argparse
import sys
from pathlib import Path

from bench_cli.exceptions import BenchError

_OWN_COMMANDS = frozenset([
    "new", "init", "start", "stop", "get-app", "new-site",
    "frappe", "build", "update", "update-config", "build-admin", "setup",
])
_OWN_GROUP_OPTIONS = frozenset(["--verbose", "--yes", "-y", "--bench", "-b", "--help", "-h"])

# Global bench name selected via -b / --bench; set in main() before dispatch.
_active_bench: str | None = None


def _cli_root() -> Path:
    import bench_cli as _pkg
    return Path(_pkg.__file__).parent.parent


def find_bench_root() -> Path:
    """
    Locate the directory containing bench.toml for the active bench.

    Resolution order:
    1. -b / --bench <name> flag → benches/<name>/
    2. Exactly one bench in benches/ → use it automatically.
    3. Walk up from cwd (fallback for edge cases).
    """
    benches_dir = _cli_root() / "benches"

    if _active_bench:
        bench_dir = benches_dir / _active_bench
        if not (bench_dir / "bench.toml").exists():
            candidates = (
                [d.name for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()]
                if benches_dir.is_dir() else []
            )
            hint = f"  Available: {', '.join(candidates)}" if candidates else "  No benches found. Run: bench new <name>"
            raise BenchError(f"Bench '{_active_bench}' not found.\n{hint}")
        return bench_dir

    if benches_dir.is_dir():
        candidates = [
            d for d in benches_dir.iterdir()
            if d.is_dir() and (d / "bench.toml").exists()
        ]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = ", ".join(d.name for d in sorted(candidates))
            raise BenchError(
                f"Multiple benches found: {names}\n"
                "Specify one with: bench -b <name> <command>"
            )

    current = Path.cwd()
    for directory in [current, *current.parents]:
        if (directory / "bench.toml").exists():
            return directory

    raise BenchError("No bench found. Create one with: bench new <name>")


def _load_bench() -> "Bench":
    from bench_cli.config.bench_config import BenchConfig
    from bench_cli.core.bench import Bench

    bench_root = find_bench_root()
    config = BenchConfig.from_file(bench_root / "bench.toml")
    return Bench(config, bench_root)


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


def _is_frappe_passthrough(args: list[str]) -> bool:
    """Forward to Frappe bench when the first meaningful token isn't ours."""
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
        return arg not in _OWN_COMMANDS
    return False


def _make_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="bench", description="Frappe bench manager")
    parser.add_argument("--verbose", action="store_true", help="Show full tracebacks on error.")
    parser.add_argument("--yes", "-y", action="store_true", help="Skip confirmation prompts.")
    parser.add_argument("--bench", "-b", metavar="NAME", default=None,
                        help="Bench to operate on (name inside benches/).")

    sub = parser.add_subparsers(dest="command")

    p_new = sub.add_parser("new", help="Create a new bench.")
    p_new.add_argument("name", help="Name for the new bench.")

    sub.add_parser("init", help="Initialise the bench.")
    sub.add_parser("start", help="Start all bench processes.")
    sub.add_parser("stop", help="Stop the running bench.")
    sub.add_parser("build", help="Rebuild assets.")
    sub.add_parser("update", help="Pull latest code and migrate sites.")
    sub.add_parser("update-config", help="Regenerate config files from bench.toml.")
    p_get = sub.add_parser("get-app", help="Clone and install an app.")
    p_get.add_argument("repo", help="Git repository URL.")
    p_get.add_argument("--branch", default="", help="Git branch to checkout.")

    p_newsite = sub.add_parser("new-site", help="Create a new site and add it to bench.toml.")
    p_newsite.add_argument("name", help="Site name (e.g. site2.localhost).")
    p_newsite.add_argument("--admin-password", default="admin", help="Frappe admin password.")
    p_newsite.add_argument("--apps", nargs="*", help="Apps to assign (defaults to framework app).")

    p_frappe = sub.add_parser("frappe", help="Run a frappe CLI command.")
    p_frappe.add_argument("args", nargs=argparse.REMAINDER)

    sub.add_parser("build-admin", help="Rebuild admin frontend assets.")

    p_setup = sub.add_parser("setup", help="Production setup commands.")
    setup_sub = p_setup.add_subparsers(dest="setup_command")
    setup_sub.add_parser("nginx")
    setup_sub.add_parser("letsencrypt")
    setup_sub.add_parser("production")

    return parser


def main() -> None:
    global _active_bench
    # Make print() output appear immediately, even before subprocess output.
    sys.stdout.reconfigure(line_buffering=True)
    args_list = sys.argv[1:]

    if _is_frappe_passthrough(args_list):
        # Strip -b/--bench from the passthrough args before forwarding to Frappe.
        bench_name, clean = _strip_bench_flag(args_list)
        _active_bench = bench_name
        try:
            from bench_cli.commands.frappe_cmd import FrappeCommand
            FrappeCommand(_load_bench()).run_raw(["frappe", *clean])
        except BenchError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        return

    # Early-dispatch 'bench frappe <args...>' before argparse so that
    # sub-options like --site don't get consumed by the top-level parser.
    bench_name, remaining = _strip_bench_flag(args_list)
    if remaining and remaining[0] == "frappe":
        _active_bench = bench_name
        verbose = "--verbose" in args_list
        try:
            from bench_cli.commands.frappe_cmd import FrappeCommand
            FrappeCommand(_load_bench()).run(tuple(remaining[1:]))
        except BenchError as e:
            print(str(e), file=sys.stderr)
            sys.exit(1)
        except Exception as e:
            if verbose:
                raise
            print(str(e), file=sys.stderr)
            sys.exit(1)
        return

    parser = _make_parser()
    args = parser.parse_args(args_list)
    _active_bench = args.bench

    verbose = getattr(args, "verbose", False)
    try:
        _dispatch(args)
    except BenchError as e:
        print(str(e), file=sys.stderr)
        sys.exit(1)
    except Exception as e:
        if verbose:
            raise
        print(str(e), file=sys.stderr)
        sys.exit(1)


def _dispatch(args: argparse.Namespace) -> None:
    cmd = args.command

    if cmd is None:
        _make_parser().print_help()
        return

    if cmd == "new":
        from bench_cli.commands.new import NewCommand
        NewCommand(_cli_root() / "benches" / args.name, args.name).run()

    elif cmd == "init":
        from bench_cli.commands.init import InitCommand
        InitCommand(_load_bench()).run()

    elif cmd == "start":
        from bench_cli.commands.run import RunCommand
        RunCommand(_load_bench()).run()

    elif cmd == "stop":
        from bench_cli.commands.stop import StopCommand
        StopCommand(_load_bench()).run()

    elif cmd == "get-app":
        _cmd_get_app(args)

    elif cmd == "new-site":
        _cmd_new_site(args)

    elif cmd == "frappe":
        from bench_cli.commands.frappe_cmd import FrappeCommand
        FrappeCommand(_load_bench()).run(tuple(args.args))

    elif cmd == "build":
        from bench_cli.commands.build import BuildCommand
        BuildCommand(_load_bench()).run()

    elif cmd == "update":
        from bench_cli.commands.update import UpdateCommand
        UpdateCommand(_load_bench(), skip_confirm=args.yes).run()

    elif cmd == "update-config":
        from bench_cli.commands.update_config import UpdateConfigCommand
        UpdateConfigCommand(_load_bench()).run()

    elif cmd == "build-admin":
        from bench_cli.commands.admin import BuildAdminCommand
        BuildAdminCommand().run()

    elif cmd == "setup":
        _dispatch_setup(args)

    else:
        _make_parser().print_help()


def _cmd_get_app(args: argparse.Namespace) -> None:
    from pathlib import PurePosixPath
    from bench_cli.config.app_config import AppConfig
    from bench_cli.core.app import App
    from bench_cli.managers.python_env_manager import PythonEnvManager

    bench = _load_bench()
    name = PurePosixPath(args.repo.rstrip("/")).name
    if name.endswith(".git"):
        name = name[:-4]

    app_cfg = AppConfig(name=name, repo=args.repo, branch=args.branch or "main")
    app = App(app_cfg, bench)

    if app.is_cloned:
        print(f"'{name}' already cloned at {app.path}, skipping clone.")
    else:
        print(f"Cloning {name}...")
        app.clone()

    print(f"Installing {name}...")
    PythonEnvManager(bench).install_app(app)

    apps_txt = bench.sites_path / "apps.txt"
    existing = apps_txt.read_text().splitlines() if apps_txt.exists() else []
    if name not in existing:
        apps_txt.write_text("\n".join(existing + [name]) + "\n")

    print(f"\n'{name}' installed successfully.")


def _cmd_new_site(args: argparse.Namespace) -> None:
    from bench_cli.config.site_config import SiteConfig
    from bench_cli.core.site import Site

    bench = _load_bench()

    # Default to framework app (first cloned app) if no apps specified
    app_names = args.apps
    if not app_names:
        framework = bench.config.framework_app.name
        app_names = [framework] if framework else []

    # Validate that requested apps are actually installed
    apps_txt = bench.sites_path / "apps.txt"
    installed = set(apps_txt.read_text().splitlines()) if apps_txt.exists() else set()
    for app_name in app_names:
        if app_name not in installed:
            raise BenchError(
                f"App '{app_name}' is not installed. Run 'bench get-app <repo>' first."
            )

    # Check site doesn't already exist
    if (bench.sites_path / args.name / "site_config.json").exists():
        raise BenchError(f"Site '{args.name}' already exists.")

    site_cfg = SiteConfig(name=args.name, apps=app_names, admin_password=args.admin_password)
    site = Site(site_cfg, bench)
    print(f"Creating site '{args.name}'...")
    site.create()

    # Refresh common_site_config.json to pick up the new default site
    bench.write_common_site_config()

    print(f"\nSite '{args.name}' created successfully.")


def _dispatch_setup(args: argparse.Namespace) -> None:
    setup_cmd = getattr(args, "setup_command", None)

    if setup_cmd == "nginx":
        from bench_cli.commands.setup.nginx import SetupNginxCommand
        SetupNginxCommand(_load_bench()).run()
    elif setup_cmd == "letsencrypt":
        from bench_cli.commands.setup.letsencrypt import SetupLetsEncryptCommand
        SetupLetsEncryptCommand(_load_bench()).run()
    elif setup_cmd == "production":
        from bench_cli.commands.setup.production import SetupProductionCommand
        SetupProductionCommand(_load_bench()).run()
    else:
        print("Usage: bench setup [nginx|letsencrypt|production]", file=sys.stderr)
        sys.exit(1)
