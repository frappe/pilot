import argparse
import sys
import typing
from pathlib import Path

from bench_cli.exceptions import BenchError

if typing.TYPE_CHECKING:
    from bench_cli.core.bench import Bench

_OWN_COMMANDS = frozenset(
    [
        "new",
        "init",
        "start",
        "stop",
        "restart",
        "get-app",
        "new-site",
        "frappe",
        "build",
        "update",
        "upgrade",
        "build-admin",
        "setup",
        "volume",
        "remove-app",
        "uninstall-app",
        "list-apps",
    ]
)
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
            candidates = [d.name for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()] if benches_dir.is_dir() else []
            hint = f"  Available: {', '.join(candidates)}" if candidates else "  No benches found. Run: bench new <name>"
            raise BenchError(f"Bench '{_active_bench}' not found.\n{hint}")
        return bench_dir

    if benches_dir.is_dir():
        candidates = [d for d in benches_dir.iterdir() if d.is_dir() and (d / "bench.toml").exists()]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            names = ", ".join(d.name for d in sorted(candidates))
            raise BenchError(f"Multiple benches found: {names}\nSpecify one with: bench -b <name> <command>")

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
    parser.add_argument("--bench", "-b", metavar="NAME", default=None, help="Bench to operate on (name inside benches/).")

    sub = parser.add_subparsers(dest="command")

    p_new = sub.add_parser("new", help="Create a new bench.")
    p_new.add_argument("name", help="Name for the new bench.")

    sub.add_parser("init", help="Initialise the bench.")
    sub.add_parser("start", help="Start all bench processes.")
    sub.add_parser("stop", help="Stop the running bench.")
    sub.add_parser("restart", help="Restart supervisor processes (production mode only).")
    p_build = sub.add_parser("build", help="Build assets (downloads pre-built if available).")
    p_build.add_argument("--force", action="store_true", help="Force a full rebuild, skipping pre-built asset download.")
    sub.add_parser("update", help="Pull latest code and migrate sites.")
    p_get = sub.add_parser("get-app", help="Clone and install an app.")
    p_get.add_argument("repo", help="Git repository URL.")
    p_get.add_argument("--branch", default="", help="Git branch to checkout.")

    p_remove = sub.add_parser("remove-app", help="Remove an app from the bench.")
    p_remove.add_argument("app", help="App name to remove.")

    p_uninstall = sub.add_parser("uninstall-app", help="Uninstall an app from a site.")
    p_uninstall.add_argument("site", help="Site name.")
    p_uninstall.add_argument("app", help="App name to uninstall.")

    sub.add_parser("list-apps", help="List apps installed in the bench.")

    p_newsite = sub.add_parser("new-site", help="Create a new site and add it to bench.toml.")
    p_newsite.add_argument("name", help="Site name (e.g. site2.localhost).")
    p_newsite.add_argument("--admin-password", default="admin", help="Frappe admin password.")
    p_newsite.add_argument("--apps", nargs="*", help="Apps to assign (defaults to framework app).")

    p_frappe = sub.add_parser("frappe", help="Run a frappe CLI command.")
    p_frappe.add_argument("args", nargs=argparse.REMAINDER)

    sub.add_parser("build-admin", help="Rebuild admin frontend assets from source.")
    sub.add_parser("upgrade", help="Pull latest bench-cli and download the admin frontend.")

    p_setup = sub.add_parser("setup", help="Production setup commands.")
    setup_sub = p_setup.add_subparsers(dest="setup_command")
    setup_sub.add_parser("config", help="Regenerate config files from bench.toml.")
    setup_sub.add_parser("nginx", help="Generate nginx config.")
    setup_sub.add_parser("letsencrypt", help="Setup Let's Encrypt SSL.")
    setup_sub.add_parser("production", help="Full production setup (nginx + supervisor).")
    setup_sub.add_parser("requirements", help="Install Python and JS requirements for all apps.")

    p_volume = sub.add_parser("volume", help="ZFS volume management commands.")
    volume_sub = p_volume.add_subparsers(dest="volume_command")
    volume_sub.add_parser("status", help="Show pool and dataset status.")

    p_snap = volume_sub.add_parser("snapshot", help="Create a snapshot.")
    p_snap.add_argument("--dataset", choices=["benches", "mariadb"], default=None, help="Dataset to snapshot (default: both).")

    p_list = volume_sub.add_parser("list-snapshots", help="List snapshots.")
    p_list.add_argument("--dataset", choices=["benches", "mariadb"], default=None, help="Dataset to list (default: both).")

    p_destroy = volume_sub.add_parser("destroy-snapshot", help="Destroy a snapshot.")
    p_destroy.add_argument("tag", help="Snapshot tag to destroy (e.g. 20250528-140000).")
    p_destroy.add_argument("--dataset", choices=["benches", "mariadb"], default="benches", help="Dataset the snapshot belongs to.")

    p_restore = volume_sub.add_parser("restore-snapshot", help="Restore a dataset to a snapshot.")
    p_restore.add_argument("tag", help="Snapshot tag to restore to (e.g. 20250528-140000).")
    p_restore.add_argument("--dataset", choices=["benches", "mariadb"], default="benches", help="Dataset to restore.")

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

    import time

    verbose = getattr(args, "verbose", False)
    _t0 = time.monotonic()
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
    elapsed = time.monotonic() - _t0
    if elapsed >= 2:
        mins, secs = divmod(int(elapsed), 60)
        print(f"\nDone in {mins}m {secs}s" if mins else f"\nDone in {secs}s")


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
        from bench_cli.commands.start import RunCommand

        RunCommand(_load_bench()).run()

    elif cmd == "stop":
        from bench_cli.commands.stop import StopCommand

        StopCommand(_load_bench()).run()

    elif cmd == "restart":
        from bench_cli.commands.restart import RestartCommand

        RestartCommand(_load_bench()).run()

    elif cmd == "get-app":
        _cmd_get_app(args)

    elif cmd == "remove-app":
        from bench_cli.commands.remove_app import RemoveAppCommand

        RemoveAppCommand(_load_bench(), args.app, skip_confirm=args.yes).run()

    elif cmd == "uninstall-app":
        from bench_cli.commands.uninstall_app import UninstallAppCommand

        UninstallAppCommand(_load_bench(), args.site, args.app).run()

    elif cmd == "list-apps":
        bench = _load_bench()
        apps_txt = bench.sites_path / "apps.txt"
        if apps_txt.exists():
            apps = [a.strip() for a in apps_txt.read_text().splitlines() if a.strip()]
        else:
            apps = [a.config.name for a in bench.apps()]
        for app in apps:
            print(app)

    elif cmd == "new-site":
        _cmd_new_site(args)

    elif cmd == "frappe":
        from bench_cli.commands.frappe_cmd import FrappeCommand

        FrappeCommand(_load_bench()).run(tuple(args.args))

    elif cmd == "build":
        from bench_cli.commands.build import BuildCommand

        BuildCommand(_load_bench(), force=args.force).run()

    elif cmd == "update":
        from bench_cli.commands.update import UpdateCommand

        UpdateCommand(_load_bench(), skip_confirm=args.yes).run()

    elif cmd == "build-admin":
        from bench_cli.commands.admin import BuildAdminCommand

        BuildAdminCommand().run()

    elif cmd == "upgrade":
        from bench_cli.commands.upgrade import UpgradeCommand

        UpgradeCommand().run()

    elif cmd == "setup":
        _dispatch_setup(args)

    elif cmd == "volume":
        _dispatch_volume(args)

    else:
        _make_parser().print_help()


def _cmd_get_app(args: argparse.Namespace) -> None:
    from bench_cli.commands.get_app import GetAppCommand

    GetAppCommand(_load_bench(), args.repo, args.branch or "main").run()


def _cmd_new_site(args: argparse.Namespace) -> None:
    from bench_cli.commands.new_site import NewSiteCommand

    bench = _load_bench()

    app_names = args.apps
    if not app_names:
        framework = bench.config.framework_app.name
        app_names = [framework] if framework else []

    NewSiteCommand(bench, args.name, app_names, args.admin_password).run()


def _dispatch_setup(args: argparse.Namespace) -> None:
    setup_cmd = getattr(args, "setup_command", None)

    if setup_cmd == "config":
        from bench_cli.commands.setup_config import UpdateConfigCommand

        UpdateConfigCommand(_load_bench()).run()
    elif setup_cmd == "nginx":
        from bench_cli.commands.setup.nginx import SetupNginxCommand

        SetupNginxCommand(_load_bench()).run()
    elif setup_cmd == "letsencrypt":
        from bench_cli.commands.setup.letsencrypt import SetupLetsEncryptCommand

        SetupLetsEncryptCommand(_load_bench()).run()
    elif setup_cmd == "production":
        from bench_cli.commands.setup.production import SetupProductionCommand
        from bench_cli.commands.admin import BuildAdminCommand

        BuildAdminCommand().run()

        SetupProductionCommand(_load_bench()).run()
    elif setup_cmd == "requirements":
        from bench_cli.commands.setup.requirements import SetupRequirementsCommand

        SetupRequirementsCommand(_load_bench()).run()
    else:
        print("Usage: bench setup [config|nginx|letsencrypt|production|requirements|volume]", file=sys.stderr)
        sys.exit(1)


def _dispatch_volume(args: argparse.Namespace) -> None:
    from bench_cli.commands.volume import (
        VolumeDestroySnapshotCommand,
        VolumeListSnapshotsCommand,
        VolumeRestoreSnapshotCommand,
        VolumeSnapshotCommand,
        VolumeStatusCommand,
    )

    bench = _load_bench()
    config = bench.config.volume
    volume_cmd = getattr(args, "volume_command", None)

    if volume_cmd == "status":
        VolumeStatusCommand(config).run()
    elif volume_cmd == "snapshot":
        VolumeSnapshotCommand(bench, args.dataset).run()
    elif volume_cmd == "list-snapshots":
        VolumeListSnapshotsCommand(config, args.dataset).run()
    elif volume_cmd == "destroy-snapshot":
        VolumeDestroySnapshotCommand(config, args.tag, args.dataset).run()
    elif volume_cmd == "restore-snapshot":
        VolumeRestoreSnapshotCommand(bench, args.tag, args.dataset).run()
    else:
        print("Usage: bench volume [setup|status|snapshot|list-snapshots|destroy-snapshot|restore-snapshot]", file=sys.stderr)
        sys.exit(1)
