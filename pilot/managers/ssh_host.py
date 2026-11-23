from __future__ import annotations

import shlex
from pathlib import Path

from pilot.exceptions import CommandError
from pilot.internal.command import run_command


class SSHHost:
    """Another host that Pilot runs commands on over SSH, with a key and no prompts."""

    connect_timeout = 10
    command_timeout = 120
    # Writes beside the target first, so a dropped connection never leaves half a file.
    _write_script = 'umask 022; cat > "$1.pilot-tmp" && chmod "$2" "$1.pilot-tmp" && mv "$1.pilot-tmp" "$1"'

    def __init__(self, address: str, user: str, key_path: str | Path) -> None:
        self.address = address
        self.user = user
        self.key_path = Path(key_path)

    @property
    def known_hosts_path(self) -> Path:
        """The host key file, kept beside the login key so this host is trusted on its own."""
        return self.key_path.with_name(f"{self.key_path.name}.known_hosts")

    def get_command(self, argv: list[str], *, sudo: bool = True) -> list[str]:
        """The local ssh argv that runs `argv` on this host."""
        remote = ["sudo", "-n", *argv] if sudo else argv
        return [
            "ssh",
            "-i",
            str(self.key_path),
            "-l",
            self.user,
            "-o",
            "BatchMode=yes",
            "-o",
            "IdentitiesOnly=yes",
            "-o",
            "StrictHostKeyChecking=accept-new",
            "-o",
            f"UserKnownHostsFile={self.known_hosts_path}",
            "-o",
            f"ConnectTimeout={self.connect_timeout}",
            self.address,
            "--",
            shlex.join(remote),
        ]

    def run(
        self,
        argv: list[str],
        *,
        sudo: bool = True,
        stdin_text: str | None = None,
        timeout: float | None = None,
    ) -> str:
        """Run `argv` on this host and return its output; raise CommandError on failure."""
        result = run_command(
            self.get_command(argv, sudo=sudo),
            stdin_text=stdin_text,
            timeout=timeout or self.command_timeout,
        )
        # A log tail can start inside a multi-byte character.
        return result.stdout.decode(errors="replace") if isinstance(result.stdout, bytes) else result.stdout

    def is_reachable(self) -> bool:
        """Whether this host accepts the login and grants sudo without a password."""
        try:
            return self.run(["true"], timeout=self.connect_timeout * 2) == ""
        except CommandError:
            return False

    def read_text(self, path: str) -> str:
        """The text of a file on this host."""
        return self.run(["cat", path])

    def exists(self, path: str) -> bool:
        """Whether a file exists on this host."""
        return self.run(["sh", "-c", 'test -e "$1" && echo yes || echo no', "sh", path]).strip() == "yes"

    def write_text(self, path: str, text: str, mode: str = "0644") -> None:
        """Replace a file on this host in one step."""
        self.run(["sh", "-c", self._write_script, "sh", path, mode], stdin_text=text)

    @property
    def total_memory_mb(self) -> int:
        """The host memory in MiB, from /proc/meminfo."""
        for line in self.read_text("/proc/meminfo").splitlines():
            name, _, value = line.partition(":")
            if name == "MemTotal":
                return int(value.split()[0]) // 1024
        raise CommandError(f"{self.address} reported no MemTotal in /proc/meminfo.")

    def get_free_disk_bytes(self, path: str) -> int:
        """Free bytes on the file system that holds `path`."""
        output = self.run(["df", "--output=avail", "-B1", path])
        return int(output.split()[-1])

    def get_client_address(self) -> str:
        """The address this host sees Pilot's connections come from."""
        # sudo would drop SSH_CLIENT from the environment.
        address = self.run(["sh", "-c", 'echo "$SSH_CLIENT"'], sudo=False).split()
        if not address:
            raise CommandError(f"{self.address} did not report the client address of the SSH session.")
        return address[0]
