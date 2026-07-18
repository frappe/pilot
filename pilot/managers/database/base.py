from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from pilot.exceptions import DatabaseError
from pilot.managers.packages import get_package_manager
from pilot.managers.platform import is_macos
from pilot.utils import run_command


class UserOwnedDBManager:
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
        return self._user_unit_dir() / self._UNIT_NAME

    def is_provisioned(self) -> bool:
        """A user unit means this per-user server has already been set up."""
        return self.unit_path.exists()

    def is_running(self) -> bool:
        if is_macos():
            return self._brew_service_running()
        result = subprocess.run(
            self._systemctl("is-active", self._UNIT_NAME),
            env=self._systemctl_env(),
            capture_output=True,
            timeout=5,
        )
        return result.returncode == 0

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

    def _systemctl(self, *args: str) -> list[str]:
        return ["systemctl", "--user", *args]

    def _systemctl_env(self) -> dict:
        # CI and su -c often miss this; systemctl --user needs it.
        env = dict(os.environ)
        if not env.get("XDG_RUNTIME_DIR"):
            env["XDG_RUNTIME_DIR"] = f"/run/user/{os.getuid()}"
        return env

    def _user_unit_dir(self) -> Path:
        return Path.home() / ".config" / "systemd" / "user"

    def _brew_package(self) -> str:
        return (
            self._installed_brew_formula() or f"{self._BREW_FORMULA_BASE}@{self._DEFAULT_VERSION}"
        )

    def _installed_brew_formula(self) -> str | None:
        """Return the installed Homebrew formula name, versioned or unversioned."""
        result = subprocess.run(["brew", "list", "--formula"], capture_output=True, text=True, timeout=10)
        if result.returncode != 0:
            return None
        formulae = result.stdout.split()
        if self._BREW_FORMULA_BASE in formulae:
            return self._BREW_FORMULA_BASE
        return next((f for f in formulae if f.startswith(f"{self._BREW_FORMULA_BASE}@")), None)

    def _brew_service_running(self) -> bool:
        result = subprocess.run(["brew", "services", "list"], capture_output=True, text=True, timeout=10)
        for line in result.stdout.splitlines():
            parts = line.split()
            if parts and parts[0] == self._brew_package() and "started" in parts:
                return True
        return False
