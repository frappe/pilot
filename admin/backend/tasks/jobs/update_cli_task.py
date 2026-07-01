from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path


def _cli_root() -> Path:
    import pilot as _pkg
    return Path(_pkg.__file__).parent.parent


def _step(key: str, label: str = "") -> None:
    print(f"STEP {key},{time.time():.3f} {label}", flush=True)


def _step_failed(key: str) -> None:
    print(f"STEP-FAILED {key},{time.time():.3f}", flush=True)


if __name__ == "__main__":
    # bench_root is passed by the task runner but not needed here
    cli_root = _cli_root()
    _step("update", f"Update bench-cli at {cli_root}")

    result = subprocess.run(
        ["git", "-C", str(cli_root), "pull"],
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
    )
    if result.returncode != 0:
        _step_failed("update")
        sys.exit(result.returncode)
    _step("done")
