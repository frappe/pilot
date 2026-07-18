from __future__ import annotations

import subprocess
from pathlib import Path

from pilot.managers.platform import _privileged

LETSENCRYPT_LIVE = Path("/etc/letsencrypt/live")


def live_cert_path(domain: str) -> Path:
    return LETSENCRYPT_LIVE / domain / "fullchain.pem"


def live_key_path(domain: str) -> Path:
    return LETSENCRYPT_LIVE / domain / "privkey.pem"


def cert_files_exist(domain: str) -> bool:
    # /etc/letsencrypt/live is root-only (0700), so stat with privilege.
    return (
        subprocess.run(
            _privileged(
                [
                    "test",
                    "-f",
                    str(live_cert_path(domain)),
                    "-a",
                    "-f",
                    str(live_key_path(domain)),
                ]
            ),
            capture_output=True,
        ).returncode
        == 0
    )
