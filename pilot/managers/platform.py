import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from pathlib import Path


class Distro(Enum):
    """Linux distributions with their own package manager support."""

    DEBIAN = "debian"
    UBUNTU = "ubuntu"
    FEDORA = "fedora"
    ARCH = "arch"
    UNKNOWN = "unknown"


class HostPlatform:
    """The operating system Pilot runs on, probed fresh on each read."""

    extra_bin_dirs = ("/usr/local/sbin", "/usr/sbin", "/sbin", "/usr/local/bin", "/usr/bin", "/bin")
    os_release_path = Path("/etc/os-release")
    nginx_config_dir = Path("/etc/nginx/conf.d")
    native_process_manager = "systemd"

    @property
    def is_macos(self) -> bool:
        """Whether this host runs macOS."""
        return platform.system() == "Darwin"

    @property
    def is_linux(self) -> bool:
        """Whether this host runs Linux, the default for anything that is not macOS."""
        return not self.is_macos

    @property
    def is_root(self) -> bool:
        """Whether Pilot runs as root."""
        return hasattr(os, "geteuid") and os.geteuid() == 0

    @property
    def has_passwordless_sudo(self) -> bool:
        """Whether privileged commands can run without a password prompt."""
        if self.is_root:
            return True
        if self.get_executable_path("sudo") is None:
            return False
        return subprocess.run(["sudo", "-n", "true"], capture_output=True, timeout=5).returncode == 0

    @property
    def os_release(self) -> dict[str, str]:
        """The fields of /etc/os-release, or an empty dict when it is missing."""
        if not self.os_release_path.exists():
            return {}
        fields = {}
        for line in self.os_release_path.read_text().splitlines():
            key, _, value = line.partition("=")
            fields[key.strip()] = value.strip().strip('"')
        return fields

    @property
    def distro(self) -> Distro:
        """The Linux distribution, falling back to ID_LIKE."""
        if not self.is_linux:
            return Distro.UNKNOWN
        fields = self.os_release
        known = {distro.value for distro in Distro}
        if fields.get("ID") in known:
            return Distro(fields["ID"])
        for token in fields.get("ID_LIKE", "").split():
            if token in known:
                return Distro(token)
        return Distro.UNKNOWN

    @property
    def os_version(self) -> str:
        """A readable OS name and version, such as "Ubuntu 24.04 LTS" or "macOS 14.5"."""
        if self.is_macos:
            version = platform.mac_ver()[0]
            return f"macOS {version}" if version else "macOS"
        if pretty_name := self.os_release.get("PRETTY_NAME"):
            return pretty_name
        return f"{platform.system()} {platform.release()}".strip()

    @property
    def kernel_version(self) -> str:
        """The kernel release, such as "6.8.0-40-generic" or Darwin's "23.5.0"."""
        return platform.release()

    @property
    def mariadb_config_bin(self) -> str | None:
        """The mariadb_config or mysql_config path, including keg-only Homebrew installs."""
        if found := (self.get_executable_path("mariadb_config") or self.get_executable_path("mysql_config")):
            return found
        prefix = self.get_command_output(["brew", "--prefix", "mariadb"])
        if prefix is None:
            return None
        candidate = Path(prefix) / "bin" / "mariadb_config"
        return str(candidate) if candidate.exists() else None

    def get_executable_path(self, name: str) -> str | None:
        """`shutil.which` that also searches the sbin and bin directories a minimal PATH omits."""
        path = os.environ.get("PATH", os.defpath)
        return shutil.which(name, path=os.pathsep.join([path, *self.extra_bin_dirs]))

    def add_mysqlclient_flags(self, env: dict) -> None:
        """Set the flags mysqlclient needs to compile against a Homebrew MariaDB."""
        if not self.is_macos or not (config_bin := self.mariadb_config_bin):
            return
        for variable, option in (("MYSQLCLIENT_CFLAGS", "--cflags"), ("MYSQLCLIENT_LDFLAGS", "--libs")):
            flags = self.get_command_output([config_bin, option])
            if flags is None:
                return
            env.setdefault(variable, flags)

    @staticmethod
    def get_command_output(argv: list[str]) -> str | None:
        """Stripped stdout of a short probe command, or None when it could not run."""
        from pilot.exceptions import CommandError
        from pilot.internal.command import run_command

        try:
            return run_command(argv, timeout=5).stdout.decode().strip()
        except (CommandError, OSError, UnicodeDecodeError):
            return None

    @staticmethod
    def get_process_start_time(pid: int) -> float | None:
        """When the process started, in epoch seconds, from /proc. None when unreadable."""
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
            boot_time = re.search(r"^btime (\d+)$", Path("/proc/stat").read_text(), re.MULTILINE)
        except OSError:
            return None
        # The command name, the second field, may hold spaces; the start time is the 20th field after it.
        fields = re.search(r"\) (.*)$", stat)
        if not boot_time or not fields:
            return None
        return int(boot_time[1]) + int(fields[1].split()[19]) / os.sysconf("SC_CLK_TCK")


class Privileges:
    """Runs commands as root through sudo, and makes sudo fail instead of prompting when asked."""

    environment_key = "PILOT_NONINTERACTIVE_PRIVILEGES"
    noninteractive: ContextVar[bool] = ContextVar("noninteractive_privileges", default=False)

    @classmethod
    def get_command(cls, command: list[str]) -> list[str]:
        """The command prefixed with sudo, unless Pilot already runs as root."""
        if HostPlatform().is_root:
            return command
        return [*(["sudo", "-n"] if cls.is_noninteractive() else ["sudo"]), *command]

    @classmethod
    def is_noninteractive(cls) -> bool:
        """Whether sudo must fail instead of prompting, set by context or by the environment."""
        return cls.noninteractive.get() or os.environ.get(cls.environment_key) == "1"

    @classmethod
    @contextmanager
    def without_prompts(cls) -> Iterator[None]:
        """Make sudo fail instead of prompting in the current execution context."""
        token = cls.noninteractive.set(True)
        try:
            yield
        finally:
            cls.noninteractive.reset(token)


class SystemService:
    """A systemd service on the host."""

    def __init__(self, name: str) -> None:
        self.name = name

    @property
    def is_running(self) -> bool:
        """Whether systemd reports the service as active."""
        try:
            command = ["systemctl", "is-active", "--quiet", self.name]
            return subprocess.run(command, capture_output=True, timeout=5).returncode == 0
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return False

    def get_command(self, action: str) -> list[str]:
        """The privileged systemctl command for an action, such as start, reload or enable."""
        return Privileges.get_command(["systemctl", action, self.name])


class HostsFile:
    """The /etc/hosts file, edited through noninteractive sudo for local development names."""

    address = "127.0.0.1"

    def __init__(self, path: Path = Path("/etc/hosts")) -> None:
        self.path = path

    def has_entry(self, hostname: str) -> bool:
        """Whether the file maps the hostname to the local address."""
        return any(self.is_entry_line(line, hostname) for line in self.path.read_text().splitlines())

    def add(self, hostname: str) -> None:
        """Map the hostname to the local address; warn instead of failing when sudo is refused."""
        if self.has_entry(hostname):
            return
        entry = f"{self.address} {hostname}"
        try:
            subprocess.run(
                ["sudo", "-n", "tee", "-a", str(self.path)],
                input=f"{entry}\n".encode(),
                capture_output=True,
                check=True,
            )
        except (subprocess.CalledProcessError, OSError) as e:
            print(
                f"Warning: could not add '{entry}' to {self.path}: {e}.\n"
                f"  Add it manually to reach the site by name.",
                file=sys.stderr,
            )

    def remove(self, hostname: str) -> None:
        """Remove every local entry for the hostname, if any."""
        try:
            lines = self.path.read_text().splitlines()
        except OSError:
            return
        kept = [line for line in lines if not self.is_entry_line(line, hostname)]
        if len(kept) == len(lines):
            return
        subprocess.run(
            ["sudo", "-n", "tee", str(self.path)],
            input=("\n".join(kept) + "\n").encode(),
            capture_output=True,
            check=False,
        )

    @classmethod
    def is_entry_line(cls, line: str, hostname: str) -> bool:
        """Whether one hosts line maps the hostname to the local address."""
        tokens = line.split("#", 1)[0].split()
        return bool(tokens) and tokens[0] == cls.address and hostname in tokens[1:]


class LegacyBindMount:
    """A bench bind mount from older Pilot versions, with its /etc/fstab line."""

    def __init__(self, target: Path, fstab_path: Path = Path("/etc/fstab")) -> None:
        self.target = target
        self.fstab_path = fstab_path

    @property
    def is_mounted(self) -> bool:
        """Whether the target is a mount point now."""
        try:
            return self.target.is_mount()
        except OSError:
            return False

    def remove(self) -> None:
        """Unmount the target and drop its fstab line. Each step only warns on failure."""
        if self.is_mounted:
            print(f"Unmounting legacy bind mount at {self.target}...", flush=True)
            try:
                subprocess.run(Privileges.get_command(["umount", "-l", str(self.target)]), check=False)
            except Exception as exc:
                print(f"  (unmount {self.target} skipped: {exc})")
        self.remove_fstab_line()

    def remove_fstab_line(self) -> None:
        """Rewrite /etc/fstab without the line that mounts the target."""
        try:
            lines = self.fstab_path.read_text().splitlines()
        except OSError:
            return
        kept = [line for line in lines if not self.is_target_line(line)]
        if len(kept) == len(lines):
            return
        try:
            subprocess.run(
                Privileges.get_command(["tee", str(self.fstab_path)]),
                input=("\n".join(kept) + "\n").encode(),
                capture_output=True,
                check=True,
            )
        except Exception as exc:
            print(f"  (fstab cleanup for {self.target} skipped: {exc})")

    def is_target_line(self, line: str) -> bool:
        """Whether an fstab line, not a comment, mounts something at the target."""
        fields = line.split()
        return len(fields) >= 2 and not line.lstrip().startswith("#") and fields[1] == str(self.target)
