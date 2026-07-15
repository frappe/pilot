from __future__ import annotations

import subprocess
import sys
import time


def _step(key: str, label: str = "") -> None:
    print(f"STEP {key},{time.time():.3f} {label}", flush=True)


def _step_failed(key: str) -> None:
    print(f"STEP-FAILED {key},{time.time():.3f}", flush=True)


if __name__ == "__main__":
    # bench_root is passed by the task runner but not needed here
    from pilot.loader import cli_root

    root = cli_root()
    _step("update", f"Update bench-cli at {root}")

    result = subprocess.run(
        ["git", "-C", str(root), "pull"],
        stdout=sys.stdout,
        stderr=sys.stderr,
        text=True,
    )
    if result.returncode != 0:
        _step_failed("update")
        sys.exit(result.returncode)
    _step("done")
