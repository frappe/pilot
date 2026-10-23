"""Tests for the JS install step of scripts/build-app-assets.sh, with stub package managers."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[2]
SCRIPT = ROOT / "scripts" / "build-app-assets.sh"

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")

# Records each call as "<tool> <args> @ <cwd relative to the build bench's apps/>".
STUB = """#!/bin/sh
echo "$(basename "$0") $* @ ${PWD#*/bench/apps/}" >> "$LOG"
"""
# Frappe's `yarn run production` runs the app's build script, which needs the installed pnpm.
YARN_STUB = (
    STUB
    + """if [ "$1" = run ]; then
    mkdir -p frappe/public/dist && echo bundle > frappe/public/dist/app.js
    echo "pnpm on PATH: $(command -v pnpm)" >> "$LOG"
fi
"""
)


def write_executable(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


def write_package(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "package.json").write_text(json.dumps({"name": path.name}))


@pytest.fixture
def environment(tmp_path: Path) -> dict[str, str]:
    """A PATH with a yarn stub and no pnpm; pnpm is only in ~/.local/bin, where Pilot installs it."""
    home = tmp_path / "home"
    write_executable(tmp_path / "bin" / "yarn", YARN_STUB)
    write_executable(home / ".local" / "bin" / "pnpm", STUB)
    return {
        "PATH": f"{tmp_path / 'bin'}:{Path(sys.executable).parent}:/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(tmp_path),
        "LOG": str(tmp_path / "calls.log"),
    }


def read_calls(environment: dict[str, str]) -> list[str]:
    log = Path(environment["LOG"])
    return log.read_text().splitlines() if log.exists() else []


def test_script_installs_with_the_lockfile_manager_and_keeps_pnpm_on_path(
    tmp_path: Path, environment: dict[str, str]
) -> None:
    # The app is frappe itself, so the script builds it without cloning frappe.
    app = tmp_path / "checkout"
    write_package(app)
    (app / "frappe").mkdir()
    (app / "frappe" / "hooks.py").write_text("")
    (app / "pnpm-lock.yaml").write_text("importers:\n\n  .: {}\n\n  frontend: {}\n")
    write_package(app / "frontend")
    write_package(app / "roster")
    (app / "pyproject.toml").write_text(
        '[[tool.bench.assets]]\nbuild_dir = "frontend"\n\n[[tool.bench.assets]]\nbuild_dir = "roster"\n'
    )
    subprocess.run(["git", "init", "-q", str(app)], check=True)
    subprocess.run(["git", "-C", str(app), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(app), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c"],
        check=True,
    )

    subprocess.run(
        ["bash", str(SCRIPT), str(app), "develop", str(tmp_path / "out")],
        env=environment,
        check=True,
        capture_output=True,
    )

    assert read_calls(environment) == [
        "pnpm install --frozen-lockfile --config.confirmModulesPurge=false @ frappe",
        "yarn install --frozen-lockfile @ frappe/roster",
        "yarn run production --apps frappe --run-build-command @ frappe",
        f"pnpm on PATH: {environment['HOME']}/.local/bin/pnpm",
    ]


@pytest.mark.parametrize("conflict_dir", ["", "frontend"])
def test_two_lockfiles_stop_the_install_before_any_manager_runs(
    tmp_path: Path, environment: dict[str, str], conflict_dir: str
) -> None:
    app = tmp_path / "app"
    write_package(app)
    (app / "yarn.lock").write_text("")
    write_package(app / "frontend")
    (app / conflict_dir / "yarn.lock").write_text("")
    (app / conflict_dir / "pnpm-lock.yaml").write_text("")

    result = subprocess.run(
        [sys.executable, "-m", "pilot.core.app.node_packages", str(app), "frontend"],
        env={**environment, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
    )

    assert result.returncode == 1
    assert "error:" in result.stderr and "yarn.lock and pnpm-lock.yaml" in result.stderr
    assert read_calls(environment) == []


# `uv venv` writes a python stub that logs its call and builds what `build_standard_apps` would.
UV_STUB = (
    STUB
    + """if [ "$1" = venv ]; then
    mkdir -p "$3/bin"
    printf '#!/bin/sh\\necho "python $3 @ ${PWD##*/}" >> "$LOG"\\nmkdir -p "$PYTHONPATH/frappe/public/app_builds/portal"\\n' > "$3/bin/python"
    chmod +x "$3/bin/python"
fi
"""
)


def test_script_builds_and_publishes_exported_studio_apps(tmp_path: Path, environment: dict[str, str]) -> None:
    app = tmp_path / "checkout"
    (app / "frappe").mkdir(parents=True)
    (app / "frappe" / "hooks.py").write_text("")
    write_package(app / "studio" / "frontend")
    (app / "studio" / "Help-Desk").mkdir()
    (app / "studio" / "Help-Desk" / "help_desk.json").write_text("{}")
    write_executable(tmp_path / "bin" / "uv", UV_STUB)
    subprocess.run(["git", "init", "-q", str(app)], check=True)
    subprocess.run(["git", "-C", str(app), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(app), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c"],
        check=True,
    )

    subprocess.run(
        ["bash", str(SCRIPT), str(app), "develop", str(tmp_path / "out")],
        env=environment,
        check=True,
        capture_output=True,
    )

    install_editor, create_env, install_frappe, build = read_calls(environment)[-4:]
    assert install_editor == "yarn install --frozen-lockfile @ frappe/studio/frontend"
    assert create_env.startswith("uv venv --quiet ") and create_env.endswith(" @ frappe")
    assert install_frappe.startswith("uv pip install --quiet --override ")
    assert install_frappe.partition(" @ ")[0].endswith("/bench/apps/frappe") and " -e " in install_frappe
    assert build == "python frappe @ sites"
    archive = next((tmp_path / "out").glob("*.tar.gz"))
    with tarfile.open(archive) as tar:
        manifest = json.load(tar.extractfile("manifest.json"))
    assert "frappe/public/app_builds" in manifest["paths"]


def test_script_builds_frappe_s_framework_and_page_islands(tmp_path: Path, environment: dict[str, str]) -> None:
    app = tmp_path / "checkout"
    (app / "frappe").mkdir(parents=True)
    (app / "frappe" / "hooks.py").write_text("")
    for script in ("build-framework.js", "build-pages.js"):
        write_executable(app / "ui" / "vite" / "island" / script, "")
    write_executable(tmp_path / "bin" / "node", STUB)
    subprocess.run(["git", "init", "-q", str(app)], check=True)
    subprocess.run(["git", "-C", str(app), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(app), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "-m", "c"],
        check=True,
    )

    subprocess.run(
        ["bash", str(SCRIPT), str(app), "develop", str(tmp_path / "out")],
        env=environment,
        check=True,
        capture_output=True,
    )

    assert [call for call in read_calls(environment) if call.startswith("node ")] == [
        "node ui/vite/island/build-framework.js --production @ frappe",
        "node ui/vite/island/build-pages.js --production @ frappe",
    ]
