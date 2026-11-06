"""Tests for NodePackages: which manager installs an app's JS dependencies, and where."""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.app.node_packages import NodePackageManager, NodePackages
from pilot.exceptions import BenchError, CommandError


@pytest.fixture
def run_command() -> Iterator[MagicMock]:
    with (
        patch.object(NodePackageManager, "bin", new=property(lambda manager: manager.name)),
        patch.object(NodePackageManager, "ensure_installed"),
        patch("pilot.core.app.node_packages.run_command") as run_command,
    ):
        yield run_command


# Modeled on Suite's lockfile: pnpm 12 writes its own environment as a first YAML document.
SUITE_PNPM_LOCK = """---
lockfileVersion: '9.0'

importers:

  .:
    packageManagerDependencies:
      pnpm:
        specifier: 12.10.1
        version: 12.10.1

packages:

  pnpm@12.10.1:
    resolution: {integrity: sha512-abc}

---
lockfileVersion: '9.0'

importers:

  .:
    dependencies:
      vue:
        specifier: ^3.5.38
        version: 3.5.39

  frontend:
    dependencies:
      frappe-ui:
        specifier: ^0.1.0
        version: 0.1.0

packages:

  vue@3.5.39:
    resolution: {integrity: sha512-def}
"""


def write_package(path: Path, **package: object) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / "package.json").write_text(json.dumps(package))
    return path


def test_yarn_lock_installs_frozen_with_yarn(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "yarn.lock").write_text("")

    NodePackages(app).install()

    run_command.assert_called_once_with(["yarn", "install", "--frozen-lockfile"], cwd=app, stream_output=True)


def test_pnpm_lock_installs_frozen_with_pnpm(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "pnpm-lock.yaml").write_text("")

    NodePackages(app).install()

    run_command.assert_called_once_with(
        ["pnpm", "install", "--frozen-lockfile", "--config.confirmModulesPurge=false"],
        cwd=app,
        stream_output=True,
    )


def test_workspace_member_installs_from_the_workspace_root(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "pnpm-lock.yaml").write_text(SUITE_PNPM_LOCK)
    frontend = write_package(app / "frontend", scripts={"build": "vite build"})

    packages = NodePackages(frontend, app)
    packages.install()

    assert run_command.call_args.args[0][0] == "pnpm"
    assert run_command.call_args.kwargs["cwd"] == app
    assert packages.get_script_command("build") == ["pnpm", "run", "build"]


def test_member_of_a_plain_app_installs_in_its_own_directory(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "pnpm-lock.yaml").write_text("")
    frontend = write_package(app / "frontend")
    (frontend / "yarn.lock").write_text("")

    NodePackages(frontend, app).install()

    run_command.assert_called_once_with(
        ["yarn", "install", "--frozen-lockfile"], cwd=frontend, stream_output=True
    )


def test_two_lockfiles_fail_before_installing(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "yarn.lock").write_text("")
    (app / "pnpm-lock.yaml").write_text("")

    with pytest.raises(BenchError, match=r"yarn\.lock and pnpm-lock\.yaml"):
        NodePackages(app).install()
    run_command.assert_not_called()


def test_failed_frozen_install_is_not_retried(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "yarn.lock").write_text("")
    run_command.side_effect = CommandError("lockfile needs update", returncode=1)

    with pytest.raises(CommandError):
        NodePackages(app).ensure_installed()
    run_command.assert_called_once()


def test_pnpm_install_runs_again_only_after_the_lockfile_changes(
    tmp_path: Path, run_command: MagicMock
) -> None:
    app = write_package(tmp_path / "app")
    lockfile, marker = "pnpm-lock.yaml", ".modules.yaml"
    (app / lockfile).write_text("")
    (app / "node_modules").mkdir()
    (app / "node_modules" / marker).write_text("")
    os.utime(app / lockfile, (1, 1))
    os.utime(app / "node_modules" / marker, (2, 2))

    NodePackages(app).ensure_installed()
    run_command.assert_not_called()

    os.utime(app / lockfile, (3, 3))
    NodePackages(app).ensure_installed()
    run_command.assert_called_once()


def test_yarn_without_a_lockfile_preserves_existing_install(tmp_path: Path, run_command: MagicMock) -> None:
    app = write_package(tmp_path / "app")
    (app / "node_modules").mkdir()
    (app / "node_modules/.yarn-integrity").write_text("")

    NodePackages(app).ensure_installed()

    run_command.assert_not_called()


def test_yarn_install_checks_contents_even_when_timestamps_match(
    tmp_path: Path, run_command: MagicMock
) -> None:
    from pilot.managers.node_dependencies import NodeDependencies

    app = write_package(tmp_path / "app")
    lock = app / "yarn.lock"
    integrity = app / "node_modules/.yarn-integrity"
    integrity.parent.mkdir()
    lock.write_text("lockfile")
    integrity.write_text("{}")
    os.utime(lock, (1, 1))
    os.utime(integrity, (2, 2))
    (integrity.parent / ".pilot-install-key").write_text(NodeDependencies.get_key(app))

    NodePackages(app).ensure_installed()
    run_command.assert_not_called()

    lock.write_text("changed lockfile")
    os.utime(lock, (1, 1))
    NodePackages(app).ensure_installed()
    run_command.assert_called_once()


@pytest.mark.parametrize("corrupted", [b'{"lockfileEntries":', b"\xff", b"[]"])
def test_corrupt_yarn_integrity_reinstalls_despite_matching_stamp(
    tmp_path: Path, run_command: MagicMock, corrupted: bytes
) -> None:
    from pilot.managers.node_dependencies import NodeDependencies

    app = write_package(tmp_path / "app")
    (app / "yarn.lock").write_text("lockfile")
    modules = app / "node_modules"
    modules.mkdir()
    integrity = modules / ".yarn-integrity"
    integrity.write_bytes(corrupted)
    key = NodeDependencies.get_key(app)
    (modules / ".pilot-install-key").write_text(key)
    assert not NodeDependencies.has_matching_install(app, key)

    def install(*args, **kwargs):
        integrity.write_text('{"lockfileEntries": {"dependency": "resolved"}}')

    run_command.side_effect = install
    NodePackages(app).ensure_installed()

    run_command.assert_called_once()
    assert NodeDependencies.has_matching_install(app, key)
    assert "resolved" in NodeDependencies.get_resolved_key(app)


def test_missing_pnpm_is_installed_into_local_before_it_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    app = write_package(tmp_path / "app")
    (app / "pnpm-lock.yaml").write_text("")
    commands: list[list[str]] = []

    def run(argv: list[str], **kwargs: object) -> None:
        commands.append(argv)
        if argv[:3] == ["npm", "install", "-g"]:
            prefix = Path(argv[argv.index("--prefix") + 1])
            (prefix / "bin").mkdir(parents=True)
            (prefix / "bin" / "pnpm").write_text("")

    with (
        patch("pilot.core.app.node_packages.shutil.which", return_value=None),
        patch("pilot.core.app.node_packages.run_command", side_effect=run),
    ):
        NodePackages(app).install()

    local = tmp_path / "home" / ".local"
    assert commands == [
        ["npm", "install", "-g", "pnpm", "--prefix", str(local)],
        [str(local / "bin" / "pnpm"), "install", "--frozen-lockfile", "--config.confirmModulesPurge=false"],
    ]


def test_directory_without_package_json_installs_nothing(tmp_path: Path, run_command: MagicMock) -> None:
    app = tmp_path / "app"
    app.mkdir()

    NodePackages(app).install()
    NodePackages(app).ensure_installed()

    run_command.assert_not_called()


def test_non_frozen_pnpm_install_replaces_node_modules_without_a_prompt(
    tmp_path: Path, run_command: MagicMock
) -> None:
    app = write_package(tmp_path / "app")
    (app / "pnpm-lock.yaml").write_text("")

    NodePackages(app).install(frozen=False)

    run_command.assert_called_once_with(
        ["pnpm", "install", "--config.confirmModulesPurge=false"], cwd=app, stream_output=True
    )


def test_pnpm_lockfile_importers_decide_which_folders_install_from_the_root(
    tmp_path: Path, run_command: MagicMock
) -> None:
    app = write_package(tmp_path / "app")
    (app / "pnpm-lock.yaml").write_text(SUITE_PNPM_LOCK)
    (app / "pnpm-workspace.yaml").write_text("packages: [\n  frontend\n]\n")
    frontend = write_package(app / "frontend")
    roster = write_package(app / "roster")

    NodePackages(frontend, app).install()
    NodePackages(roster, app).install()

    assert [(call.args[0][0], call.kwargs["cwd"]) for call in run_command.call_args_list] == [
        ("pnpm", app),
        ("yarn", roster),
    ]


@pytest.mark.parametrize(
    "workspaces",
    [["frontend"], {"packages": ["./frontend/"]}, ["*", "!./roster"]],
)
def test_package_json_workspaces_decide_which_folders_install_from_the_root(
    tmp_path: Path, run_command: MagicMock, workspaces: object
) -> None:
    app = write_package(tmp_path / "app", workspaces=workspaces)
    (app / "yarn.lock").write_text("")
    frontend = write_package(app / "frontend")
    roster = write_package(app / "roster")

    NodePackages(frontend, app).install()
    NodePackages(roster, app).install()

    assert [call.kwargs["cwd"] for call in run_command.call_args_list] == [app, roster]


def test_workspace_member_installs_from_a_root_without_a_lockfile(
    tmp_path: Path, run_command: MagicMock
) -> None:
    app = write_package(tmp_path / "app", workspaces=["frontend"])
    frontend = write_package(app / "frontend")

    NodePackages(frontend, app).install()

    run_command.assert_called_once_with(["yarn", "install", "--frozen-lockfile"], cwd=app, stream_output=True)
