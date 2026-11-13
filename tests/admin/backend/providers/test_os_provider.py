from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from admin.backend.providers import os as os_provider
from tests.pilot.commands.test_commands import make_bench


def test_the_frappe_version_runs_the_bench_python_with_its_safe_argv(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    (bench.env_path / "bin").mkdir(parents=True)
    bench.python.touch()
    (bench.env_path / "pyvenv.cfg").write_text("version_info = 3.14.2\n")
    with patch.object(os_provider, "run_command") as run_command:
        run_command.return_value.stdout = b"16.0.0\n"
        version = os_provider.OSProvider(bench.path, bench.config).frappe_version

    assert version == "16.0.0"
    assert run_command.call_args.args[0][:2] == [str(bench.python), "-P"]


def test_the_frappe_version_is_empty_without_a_venv(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)

    assert os_provider.OSProvider(bench.path, bench.config).frappe_version == ""
