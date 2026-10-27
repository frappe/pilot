from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from pilot.managers.environment import PythonEnvManager


@pytest.mark.parametrize("manager", ["yarn", "pnpm"])
@pytest.mark.parametrize("on_path", [True, False])
def test_build_env_preserves_selected_node(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, manager: str, on_path: bool
) -> None:
    selected = tmp_path / "node24"
    tools = tmp_path / ".local" / "bin"
    selected.mkdir()
    tools.mkdir(parents=True)
    for directory, version in ((selected, "v24.22.1"), (tools, "v22.23.3")):
        node = directory / "node"
        node.write_text(f"#!/bin/sh\necho {version}\n")
        node.chmod(0o755)
    package_manager = tools / manager
    package_manager.write_text("#!/bin/sh\nexec node --version\n")
    package_manager.chmod(0o755)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PATH", os.pathsep.join(map(str, [selected, tools] if on_path else [selected])))

    env = PythonEnvManager(MagicMock())._build_env()

    assert shutil.which("node", path=env["PATH"]) == str(selected / "node")
    result = subprocess.run([manager, "--version"], env=env, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == "v24.22.1"
