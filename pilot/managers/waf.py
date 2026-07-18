from __future__ import annotations

import hashlib
import shutil
import tarfile
import tempfile
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.managers.packages import get_package_manager
from pilot.managers.platform import _privileged, is_linux
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.bench import Bench

# Shared, read-only CRS assets; every bench's generated main.conf Includes these,
# so the layout is fixed and identical across distros.
SHARED_MODSEC_DIR = Path("/usr/share/nginx/modsecurity-crs")
MODSEC_MODULE_NAME = "ngx_http_modsecurity_module.so"
_MODULE_DIRS = ("/usr/lib/nginx/modules", "/usr/lib64/nginx/modules")

# Pinned OWASP CRS (the 4.x LTS line) so every host runs an identical,
# reproducible rule set regardless of what - or whether - the distro packages it.
# The immutable release asset (not the auto-generated source archive, whose bytes
# GitHub does not guarantee stable) is verified against a hardcoded SHA-256 before
# it is extracted into a privileged system directory. The digest was confirmed
# against the OWASP CRS PGP signing key (36006F0E0BA167832158821138EEACA1AB8A6E72,
# security@coreruleset.org) at pin time.
CRS_VERSION = "4.25.0"
_CRS_URL = (
    f"https://github.com/coreruleset/coreruleset/releases/download/"
    f"v{CRS_VERSION}/coreruleset-{CRS_VERSION}-minimal.tar.gz"
)
_CRS_SHA256 = "409a0da1f4daed0719150fcee5173c351e08ffa33b5b2f8936f30968b3ad4ff0"


class WafManager:
    """Installs the ModSecurity nginx module and shared CRS assets."""

    # Canonical (apt-style) name; per-distro names live in each package manager's
    # package_aliases map.
    MODULE_PACKAGE = "modsecurity-nginx"

    def __init__(self, bench: "Bench | None" = None) -> None:
        self.bench = bench

    @staticmethod
    def module_available() -> bool:
        """True if nginx can load the module - the .so is on disk or a
        modules-enabled drop-in (the Debian package's mechanism) references it."""
        if any((Path(base) / MODSEC_MODULE_NAME).exists() for base in _MODULE_DIRS):
            return True
        modules_dir = Path("/etc/nginx/modules-enabled")
        return modules_dir.is_dir() and any("modsecurity" in entry.name for entry in modules_dir.iterdir())

    @staticmethod
    def module_path() -> str | None:
        for base in _MODULE_DIRS:
            candidate = Path(base) / MODSEC_MODULE_NAME
            if candidate.exists():
                return str(candidate)
        return None

    @staticmethod
    def crs_available() -> bool:
        return (SHARED_MODSEC_DIR / "crs-setup.conf").exists() and (SHARED_MODSEC_DIR / "rules").is_dir()

    @classmethod
    def is_installed(cls) -> bool:
        """Both halves must be present before a vhost may reference the WAF."""
        return cls.module_available() and cls.crs_available()

    def install(self) -> None:
        if not is_linux():
            raise RuntimeError("The WAF (ModSecurity) is only supported on Linux production hosts.")
        if not self.module_available():
            get_package_manager().install(self.MODULE_PACKAGE)
        if not self.crs_available():
            self._install_crs()

    def _install_crs(self) -> None:
        """Download the pinned CRS release into the shared ModSecurity dir."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            archive = tmp_path / "crs.tar.gz"
            urllib.request.urlretrieve(_CRS_URL, archive)
            self._verify_checksum(archive)
            with tarfile.open(archive) as tar:
                tar.extractall(tmp_path, filter="data")
            # The archive holds a single top-level dir (coreruleset-<version>/);
            # find it rather than assume the name, in case a future pin differs.
            extracted = next(entry for entry in tmp_path.iterdir() if entry.is_dir())
            staged_setup = tmp_path / "crs-setup.conf"
            shutil.copy(extracted / "crs-setup.conf.example", staged_setup)
            run_command(_privileged(["mkdir", "-p", str(SHARED_MODSEC_DIR)]))
            run_command(_privileged(["cp", str(staged_setup), str(SHARED_MODSEC_DIR / "crs-setup.conf")]))
            run_command(
                _privileged(["cp", "-rT", str(extracted / "rules"), str(SHARED_MODSEC_DIR / "rules")])
            )

    @staticmethod
    def _verify_checksum(archive: Path) -> None:
        """Abort before extraction if the archive digest changed."""
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        if digest != _CRS_SHA256:
            raise RuntimeError(
                f"CRS archive checksum mismatch: expected {_CRS_SHA256}, got {digest}. "
                f"Refusing to install potentially tampered WAF rules."
            )
