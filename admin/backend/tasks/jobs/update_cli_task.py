from __future__ import annotations

import subprocess
import sys
from pathlib import Path


def _cli_root() -> Path:
    import bench_cli as _pkg
    return Path(_pkg.__file__).parent.parent


if __name__ == "__main__":
    # bench_root is passed by the task runner but not needed here
    cli_root = _cli_root()
    print(f"Updating bench-cli at {cli_root}...")
    sys.stdout.flush()

    result = subprocess.run(
        ["git", "-C", str(cli_root), "pull"],
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
    )
    sys.exit(result.returncode)
