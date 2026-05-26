"""
Shared helper: install app-root JS deps and build assets for a frappe app.

Frontend installs (app/frontend/) are handled by the CLI build command, not here.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def build_app_assets(bench_root: Path, app_name: str) -> None:
    app_dir = bench_root / "apps" / app_name
    sites_dir = bench_root / "sites"
    bench_bin = str(bench_root / "env" / "bin" / "bench")

    if not (app_dir / "package.json").exists():
        return

    print(f"\n[{app_name}] Installing app-root JS dependencies...")
    sys.stdout.flush()
    subprocess.run(
        ["yarn", "install", "--ignore-scripts"],
        cwd=str(app_dir),
        check=False,
    )

    print(f"\n[{app_name}] Building assets...")
    sys.stdout.flush()
    subprocess.run(
        [bench_bin, "frappe", "build", "--app", app_name],
        cwd=str(sites_dir),
        check=False,
    )
