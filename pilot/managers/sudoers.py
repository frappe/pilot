from __future__ import annotations

from pathlib import Path

from pilot.internal.command import run_command
from pilot.managers.platform import Privileges


def stage_and_copy(stage_dir: Path, content: str, target: Path, validate: list[str] | None = None) -> None:
    """Sudo-copy content into a root-owned target via a caller-owned staging file.
    `validate`, if given, is a command run against the staged file before it's
    copied into place - e.g. ["visudo", "-cf"] to catch bad sudoers syntax."""
    staged = stage_dir / target.name
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(content)
    if validate:
        run_command(Privileges.get_command([*validate, str(staged)]))
    run_command(Privileges.get_command(["cp", str(staged), str(target)]))
    staged.unlink()


def install_sudoers_grant(
    stage_dir: Path,
    bench_user: str,
    name: str,
    commands: list[str],
    bare_commands: list[str] | None = None,
) -> None:
    """Give bench_user passwordless sudo for exactly `commands`. sudo-rs rejects wildcards
    in arguments, so there `bare_commands` grants the same binaries without limits."""
    from pilot.exceptions import CommandError

    sudoers_file = Path(f"/etc/sudoers.d/{bench_user}-pilot-{name}")
    try:
        stage_and_copy(stage_dir, _grant(bench_user, commands), sudoers_file, validate=["visudo", "-cf"])
    except CommandError:
        if not bare_commands or not is_sudo_rs():
            raise
        print(f"Warning: this sudo rejects argument wildcards, so {sudoers_file.name} grants its commands without argument limits.")
        stage_and_copy(stage_dir, _grant(bench_user, bare_commands), sudoers_file, validate=["visudo", "-cf"])
    run_command(Privileges.get_command(["chmod", "440", str(sudoers_file)]))


def is_sudo_rs() -> bool:
    """Whether sudo is sudo-rs."""
    import subprocess

    result = subprocess.run(["sudo", "--version"], capture_output=True, text=True, timeout=5)
    return result.stdout.startswith("sudo-rs")


def _grant(bench_user: str, commands: list[str]) -> str:
    """The sudoers line for the commands."""
    return f"{bench_user} ALL=(ALL) NOPASSWD: " + ",".join(commands) + "\n"


def has_passwordless_sudo_for(command: list[str]) -> bool:
    """True when `sudo -n -l <command>` shows it's grantable without a password prompt."""
    import subprocess

    from pilot.managers.platform import HostPlatform

    if HostPlatform().is_root:
        return True
    if HostPlatform().get_executable_path("sudo") is None:
        return False
    result = subprocess.run(["sudo", "-n", "-l", *command], capture_output=True, timeout=5)
    return result.returncode == 0
