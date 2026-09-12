from __future__ import annotations

import subprocess
import time
from pathlib import Path

from pilot.exceptions import DatabaseError
from pilot.managers.packages import get_package_manager
from pilot.managers.platform import is_macos
from pilot.managers.systemd_user import SystemdUserMixin
from pilot.utils import run_command


class UserOwnedDBManager(SystemdUserMixin):
    """Shared service-control for per-user database servers."""

    _UNIT_NAME: str = ""
    _DISPLAY_NAME: str = ""
    _SYSTEM_PACKAGE: str = ""
    _BREW_FORMULA_BASE: str = ""
    _DEFAULT_VERSION: str = ""

    def is_installed(self) -> bool:
        raise NotImplementedError

    def is_reachable(self) -> bool:
        raise NotImplementedError

    def _wait_until_reachable(self, timeout: float = 30.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_reachable():
                return
            time.sleep(0.5)
        raise DatabaseError(f"{self._DISPLAY_NAME} did not become reachable within {timeout:.0f}s.")

    def install(self) -> None:
        if self.is_installed():
            return
        if is_macos():
            get_package_manager().install(self._brew_package())
            return
        raise DatabaseError(
            f"{self._DISPLAY_NAME} is not installed. Re-run install.sh as root to "
            f"install it (it provisions {self._SYSTEM_PACKAGE} for every supported "
            f"distro), or install '{self._SYSTEM_PACKAGE}' yourself."
        )

    @property
    def unit_path(self) -> Path:
        return self.user_unit_dir / self._UNIT_NAME

    def is_provisioned(self) -> bool:
        """A user unit means this per-user server has already been set up."""
        return self.unit_path.exists()

    def is_running(self) -> bool:
        if is_macos():
            return self._is_brew_service_running()
        result = subprocess.run(
            self._systemctl("is-active", self._UNIT_NAME),
            env=self._systemctl_env(),
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0

    def enable_at_boot(self) -> None:
        """Run on every provision, not just the first: a unit that exists but was
        never enabled starts here and would otherwise die at the next reboot."""
        self._reset_failed_state()
        run_command(self._systemctl("enable", "--now", self._UNIT_NAME), env=self._systemctl_env())

    def start(self) -> None:
        self._control("start")

    def restart(self) -> None:
        self._control("restart")

    def stop(self) -> None:
        self._control("stop")

    def _control(self, action: str) -> None:
        if is_macos():
            run_command(["brew", "services", action, self._brew_package()])
        else:
            run_command(self._systemctl(action, self._UNIT_NAME), env=self._systemctl_env())

    def _reset_failed_state(self) -> None:
        """Clear a stuck failed/rate-limited unit before (re)starting it.

        A never-loaded unit makes this exit non-zero ("not loaded") - that's
        not a failure worth raising on, so this doesn't use run_command.
        """
        subprocess.run(
            self._systemctl("reset-failed", self._UNIT_NAME),
            env=self._systemctl_env(),
            capture_output=True,
            timeout=5,
        )

    def _brew_package(self) -> str:
        return self._installed_brew_formula() or f"{self._BREW_FORMULA_BASE}@{self._DEFAULT_VERSION}"

    def _installed_brew_formula(self) -> str | None:
        """Return the installed Homebrew formula name, versioned or unversioned."""
        result = subprocess.run(["brew", "list", "--formula"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return None
        formulae = result.stdout.split()
        if self._BREW_FORMULA_BASE in formulae:
            return self._BREW_FORMULA_BASE
        return next((f for f in formulae if f.startswith(f"{self._BREW_FORMULA_BASE}@")), None)

    def _is_brew_service_running(self) -> bool:
        result = subprocess.run(["brew", "services", "list"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts and parts[0] == self._brew_package() and "started" in parts:
                return True
        return False
