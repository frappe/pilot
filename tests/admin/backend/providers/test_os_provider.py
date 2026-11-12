from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from admin.backend.providers import os as os_provider
from admin.backend.providers.os import OSProvider


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
