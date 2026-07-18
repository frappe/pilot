from __future__ import annotations

import subprocess
from pathlib import Path

from pilot.managers.platform import _privileged


def cert_files_exist(live_dir: Path) -> bool:
    # /etc/letsencrypt/live is root-only (0700), so stat with privilege.
    return (
        subprocess.run(
            _privileged(
                [
                    "test",
                    "-f",
                    str(live_dir / "fullchain.pem"),
                    "-a",
                    "-f",
                    str(live_dir / "privkey.pem"),
                ]
            ),
            capture_output=True,
        ).returncode
        == 0
    )
