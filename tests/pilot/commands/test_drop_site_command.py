from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.commands.runtime.frappe import FrappeCommand
from pilot.commands.sites.drop import DropSiteCommand
from pilot.exceptions import BenchError, TaskConflictError
from pilot.internal.cli.dispatch import forwarded_frappe_args
from pilot.internal.tasks.store import TaskStore
from tests.pilot.commands.test_commands import make_bench


def make_bench_with_site(tmp_path: Path):
    bench = make_bench(tmp_path)
    (bench.sites_path / "site1.localhost").mkdir(parents=True)
    (bench.sites_path / "site1.localhost" / "site_config.json").write_text("{}")
    return bench


@pytest.mark.parametrize(("no_backup", "force"), [(False, False), (True, False), (False, True)])
def test_drop_site_runs_pilots_drop(tmp_path: Path, no_backup: bool, force: bool) -> None:
    bench = make_bench_with_site(tmp_path)

    with patch("pilot.core.site.Site.drop") as drop:
        DropSiteCommand(
            bench, site_name="site1.localhost", no_backup=no_backup, force=force, skip_confirm=True
        ).run()

    assert (drop.call_args.kwargs["no_backup"], drop.call_args.kwargs["force"]) == (no_backup, force)


def test_drop_site_aborts_unless_confirmed(tmp_path: Path) -> None:
    bench = make_bench_with_site(tmp_path)

    with (
        patch("builtins.input", return_value="n"),
        patch("pilot.core.site.Site.drop") as drop,
        pytest.raises(BenchError, match="Aborted"),
    ):
        DropSiteCommand(bench, site_name="site1.localhost").run()

    drop.assert_not_called()


def test_drop_site_refuses_a_missing_site(tmp_path: Path) -> None:
    with patch("pilot.core.site.Site.drop") as drop, pytest.raises(BenchError, match="does not exist"):
        DropSiteCommand(make_bench(tmp_path), site_name="site1.localhost", skip_confirm=True).run()

    drop.assert_not_called()


def test_drop_site_refuses_while_a_task_uses_the_site(tmp_path: Path) -> None:
    bench = make_bench_with_site(tmp_path)
    TaskStore(bench.path).create_queued(
        {"task_id": "20260715-120000-aabbcc", "command": "backup-site", "args": {"site": "site1.localhost"}},
        resource_key="site:site1.localhost",
    )

    with (
        patch("pilot.core.site.Site.drop") as drop,
        pytest.raises(TaskConflictError, match=r"site:site1\.localhost"),
    ):
        DropSiteCommand(bench, site_name="site1.localhost", skip_confirm=True).run()

    drop.assert_not_called()


def test_drop_site_refuses_while_the_site_is_reserved(tmp_path: Path) -> None:
    bench = make_bench_with_site(tmp_path)

    with (
        bench.site("site1.localhost").reserve(),
        patch("pilot.core.site.Site.drop") as drop,
        pytest.raises(TaskConflictError, match=r"site:site1\.localhost"),
    ):
        DropSiteCommand(bench, site_name="site1.localhost", skip_confirm=True).run()

    drop.assert_not_called()


def test_drop_site_holds_the_site_while_dropping(tmp_path: Path) -> None:
    bench = make_bench_with_site(tmp_path)

    def queue_backup(**kwargs) -> None:
        TaskStore(bench.path).create_queued(
            {"task_id": "20260715-120000-aabbcc", "command": "backup-site", "args": {}},
            resource_key="site:site1.localhost",
        )

    with (
        patch("pilot.core.site.Site.drop", side_effect=queue_backup),
        pytest.raises(TaskConflictError, match=r"site:site1\.localhost"),
    ):
        DropSiteCommand(bench, site_name="site1.localhost", skip_confirm=True).run()


def test_drop_site_is_a_pilot_command() -> None:
    assert forwarded_frappe_args(["drop-site", "site1.localhost"]) is None


@pytest.mark.parametrize(
    "args",
    [
        ("--site", "site1.localhost", "drop-site", "site1.localhost", "--force", "--no-backup"),
        ("drop-site", "site1.localhost", "--no-backup", "--force", "--root-password", "secret"),
        (
            "--site",
            "site1.localhost",
            "drop-site",
            "--mariadb-root-username",
            "root",
            "--force",
            "--no-backup",
        ),
    ],
)
def test_frappes_drop_site_runs_pilots_drop_with_its_options(tmp_path: Path, args: tuple[str, ...]) -> None:
    bench = make_bench_with_site(tmp_path)

    with patch("subprocess.run") as run, patch("pilot.core.site.Site.drop") as drop:
        FrappeCommand(bench, args=args).run()

    run.assert_not_called()
    options = drop.call_args.kwargs
    assert (options["no_backup"], options["force"], options["archived_sites_path"]) == (True, True, None)


def test_frappes_drop_site_without_a_site_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(BenchError, match="Usage: pilot drop-site SITE"):
        DropSiteCommand.from_frappe_args(make_bench(tmp_path), ("drop-site", "--force"))


@pytest.mark.parametrize(
    ("no_backup", "force", "flags"),
    [(False, False, []), (True, False, ["--no-backup", "--force"]), (False, True, ["--force"])],
)
def test_site_drop_passes_frappes_flags(
    tmp_path: Path, no_backup: bool, force: bool, flags: list[str]
) -> None:
    from contextlib import nullcontext

    bench = make_bench(tmp_path)
    (bench.sites_path / "site1.localhost").mkdir(parents=True)
    (bench.sites_path / "site1.localhost" / "site_config.json").write_text("{}")
    site = bench.site("site1.localhost")

    with (
        patch("pilot.core.site.run_command") as run_command,
        patch("pilot.core.site.commands.SiteCommands.setup_credentials", return_value=nullcontext([])),
        patch("pilot.core.site.Site._provider_domains", return_value=[]),
        patch("pilot.core.site.Site._remove_from_bench_toml"),
        patch("pilot.core.site.Site._release_domains"),
        patch("pilot.managers.nginx.NginxManager.reload_for_site_change"),
        patch("pilot.core.site.CronManager"),
    ):
        site.drop(no_backup=no_backup, force=force)

    assert run_command.call_args.args[0][-len(flags) - 1 :] == ["site1.localhost", *flags]
