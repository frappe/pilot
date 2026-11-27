from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from admin.backend.providers.server import os as os_provider
from admin.backend.providers.server.os import OSProvider
from pilot.exceptions import DatabaseError


def _provider(bench_root: Path) -> OSProvider:
    config = SimpleNamespace(python_version="3.14", redis=SimpleNamespace(version=""))
    return OSProvider(bench_root, config)  # type: ignore[arg-type]


def test_frappe_version_comes_from_dist_info(tmp_path: Path) -> None:
    with patch("pilot.core.bench.Bench") as bench:
        bench.return_value.app.return_value.installed_version = "16.56.0"
        assert _provider(tmp_path).frappe_version == "16.56.0"


def test_a_bench_without_frappe_has_no_frappe_version(tmp_path: Path) -> None:
    from pilot.exceptions import BenchError

    with patch("pilot.core.bench.Bench") as bench:
        bench.return_value.app.side_effect = BenchError("App frappe not found in bench")
        assert _provider(tmp_path).frappe_version == ""


def test_a_binary_version_is_probed_once_until_the_binary_changes(tmp_path: Path) -> None:
    binary = tmp_path / "nginx"
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    os_provider._binary_version.cache_clear()
    result = SimpleNamespace(stdout=b"", stderr=b"nginx version: nginx/1.28.0")
    with patch.object(os_provider, "run_command", return_value=result) as run:
        provider = _provider(tmp_path)
        assert provider.get_flag_version(str(binary), ["-v"], r"nginx/([\d.]+)") == "1.28.0"
        assert provider.get_flag_version(str(binary), ["-v"], r"nginx/([\d.]+)") == "1.28.0"
        assert run.call_count == 1

        stat = binary.stat()
        os.utime(binary, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
        provider.get_flag_version(str(binary), ["-v"], r"nginx/([\d.]+)")
        assert run.call_count == 2


@pytest.mark.parametrize(
    ("answer", "version"),
    [
        pytest.param("11.8.3-MariaDB-ubu2404", "11.8.3", id="reachable"),
        pytest.param(DatabaseError("refused"), "", id="unreachable"),
    ],
)
def test_a_remote_mariadb_reports_the_version_the_server_gives(answer: object, version: str) -> None:
    config = SimpleNamespace(mariadb=SimpleNamespace(remote=True, existing=False))
    with patch("pilot.core.database.make_database") as make_database:
        make_database.return_value.get_scalar.side_effect = [answer]
        assert OSProvider(Path("/bench"), config).mariadb_version == version  # type: ignore[arg-type]
    make_database.return_value.get_scalar.assert_called_once_with("SELECT VERSION()")


def test_a_local_mariadb_version_comes_from_its_binary(tmp_path: Path) -> None:
    config = SimpleNamespace(mariadb=SimpleNamespace(remote=False, existing=False))
    with patch.object(OSProvider, "get_flag_version", return_value="11.8.3") as get_flag_version:
        assert OSProvider(tmp_path, config).mariadb_version == "11.8.3"  # type: ignore[arg-type]
    get_flag_version.assert_called_once_with("mariadbd", ["--version"], r"Ver ([\d.]+)")
