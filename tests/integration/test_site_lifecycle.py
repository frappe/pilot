from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pilot.core.bench import Bench


@pytest.mark.integration
def test_restore_missing_directories_then_drop_site(bench_root: Path, bench_bin: str) -> None:
    name = "test.local"
    site_path = bench_root / "sites" / name
    assert not site_path.exists()

    try:
        created = subprocess.run(
            [bench_bin, "-b", bench_root.name, "new-site", name, "--admin-password", "admin"],
            cwd=bench_root,
            capture_output=True,
            text=True,
        )
        assert created.returncode == 0, created.stderr

        site = Bench(bench_root).site(name)
        config = json.loads((site_path / "site_config.json").read_text())
        _, backups = site.backups.take(with_files=False)
        database = next(path for path in backups if path.name.endswith((".sql.gz", ".sql")))

        for directory in ("logs", "locks"):
            path = site_path / directory
            if path.is_dir():
                shutil.rmtree(path)

        site.restore(str(database))
        assert (site_path / "logs").is_dir()
        assert (site_path / "locks").is_dir()
        marker = site_path / "logs" / "keep.log"
        marker.write_text("keep")

        site.restore(str(database))
        assert marker.read_text() == "keep"
        assert json.loads((site_path / "site_config.json").read_text()) == config

        dropped = subprocess.run(
            [bench_bin, "--yes", "-b", bench_root.name, "drop-site", name],
            cwd=bench_root,
            capture_output=True,
            text=True,
        )
        assert dropped.returncode == 0, dropped.stderr
        assert not site_path.exists()
    finally:
        if site_path.exists():
            cleanup = subprocess.run(
                [bench_bin, "--yes", "-b", bench_root.name, "drop-site", name, "--no-backup"],
                cwd=bench_root,
                capture_output=True,
                text=True,
            )
            if cleanup.returncode != 0 or site_path.exists():
                message = f"Could not clean up {name}: {cleanup.stderr}"
                if error := sys.exception():
                    error.add_note(message)
                else:
                    pytest.fail(message)
