import sys
from pathlib import Path

import click

from bench2.exceptions import Bench2Error, BenchError


def find_bench_root() -> Path:
    current = Path.cwd()
    for directory in [current, *current.parents]:
        if (directory / "bench.yml").exists():
            return directory
    raise BenchError("No bench.yml found in current directory or any parent directory.")


def _load_bench() -> "Bench":
    from bench2.config.bench_config import BenchConfig
    from bench2.core.bench import Bench

    bench_root = find_bench_root()
    config = BenchConfig.from_file(bench_root / "bench.yml")
    return Bench(config, bench_root)


@click.group()
@click.option("--verbose", is_flag=True, default=False, help="Show full tracebacks on error.")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation prompts.")
@click.pass_context
def cli(context: click.Context, verbose: bool, yes: bool) -> None:
    context.ensure_object(dict)
    context.obj["verbose"] = verbose
    context.obj["yes"] = yes


@cli.command()
@click.pass_context
def new(context: click.Context) -> None:
    try:
        from bench2.commands.new import NewCommand
        NewCommand(Path.cwd()).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def init(context: click.Context) -> None:
    try:
        from bench2.commands.init import InitCommand
        bench = _load_bench()
        InitCommand(bench).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def start(context: click.Context) -> None:
    try:
        from bench2.commands.run import RunCommand
        bench = _load_bench()
        RunCommand(bench).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def stop(context: click.Context) -> None:
    try:
        from bench2.commands.stop import StopCommand
        bench = _load_bench()
        StopCommand(bench).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)


@cli.command("kill-orphaned")
@click.pass_context
def kill_orphaned(context: click.Context) -> None:
    """Kill orphaned bench processes left behind by a crashed or force-killed bench."""
    try:
        from bench2.commands.kill_orphaned import KillOrphanedCommand
        bench = _load_bench()
        yes = context.obj.get("yes", False)
        KillOrphanedCommand(bench, skip_confirm=yes).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)


@cli.command("start-admin")
@click.option("--port", default=None, type=int, help="Port for the admin interface (overrides bench.yml admin.port).")
@click.pass_context
def start_admin(context: click.Context, port: int | None) -> None:
    """Start the admin UI as a background daemon."""
    try:
        from bench2.commands.start_admin import StartAdminCommand
        bench = _load_bench()
        StartAdminCommand(bench, port=port).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)


@cli.command("stop-admin")
@click.pass_context
def stop_admin(context: click.Context) -> None:
    """Stop the background admin UI."""
    try:
        from bench2.commands.stop_admin import StopAdminCommand
        bench = _load_bench()
        StopAdminCommand(bench).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def build(context: click.Context) -> None:
    try:
        from bench2.commands.build import BuildCommand
        bench = _load_bench()
        BuildCommand(bench).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)


@cli.command()
@click.pass_context
def update(context: click.Context) -> None:
    try:
        from bench2.commands.update import UpdateCommand
        bench = _load_bench()
        yes = context.obj.get("yes", False)
        UpdateCommand(bench, skip_confirm=yes).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)


@cli.command()
@click.option("--port", default=8001, type=int, help="Port for the admin interface.")
@click.option("--host", default="127.0.0.1", help="Host for the admin interface.")
@click.pass_context
def admin(context: click.Context, port: int, host: str) -> None:
    """Start the admin web interface."""
    from bench2.admin.app import create_app
    bench_root = find_bench_root()
    app = create_app(bench_root)
    app.run(host=host, port=port, threaded=True)


@cli.group()
def setup() -> None:
    pass


@setup.command("nginx")
@click.pass_context
def setup_nginx(context: click.Context) -> None:
    try:
        from bench2.commands.setup.nginx import SetupNginxCommand
        bench = _load_bench()
        SetupNginxCommand(bench).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)


@setup.command("letsencrypt")
@click.pass_context
def setup_letsencrypt(context: click.Context) -> None:
    try:
        from bench2.commands.setup.letsencrypt import SetupLetsEncryptCommand
        bench = _load_bench()
        SetupLetsEncryptCommand(bench).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)


@setup.command("production")
@click.pass_context
def setup_production(context: click.Context) -> None:
    try:
        from bench2.commands.setup.production import SetupProductionCommand
        bench = _load_bench()
        SetupProductionCommand(bench).run()
    except Bench2Error as error:
        click.echo(str(error), err=True)
        sys.exit(1)
