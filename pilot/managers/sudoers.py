from __future__ import annotations

from pathlib import Path

from pilot.managers.platform import _privileged
from pilot.utils import run_command


def stage_and_copy(stage_dir: Path, content: str, target: Path, validate: list[str] | None = None) -> None:
    """Sudo-copy content into a root-owned target via a caller-owned staging file.
    `validate`, if given, is a command run against the staged file before it's
    copied into place - e.g. ["visudo", "-cf"] to catch bad sudoers syntax."""
    staged = stage_dir / target.name
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_text(content)
    if validate:
        run_command(_privileged([*validate, str(staged)]))
    run_command(_privileged(["cp", str(staged), str(target)]))
    staged.unlink()


def install_sudoers_grant(stage_dir: Path, bench_user: str, name: str, commands: list[str]) -> None:
    """Give bench_user passwordless sudo for exactly `commands`, no more.
    Idempotent: same deterministic content every call."""
    sudoers_file = Path(f"/etc/sudoers.d/{bench_user}-pilot-{name}")
    content = f"{bench_user} ALL=(ALL) NOPASSWD: " + ",".join(commands) + "\n"
    stage_and_copy(stage_dir, content, sudoers_file, validate=["visudo", "-cf"])
    run_command(_privileged(["chmod", "440", str(sudoers_file)]))


def has_passwordless_sudo_for(command: list[str]) -> bool:
    """True when `sudo -n -l <command>` shows it's grantable without a password prompt."""
    import subprocess

    from pilot.managers.platform import is_root, which

    if is_root():
        return True
    if which("sudo") is None:
        return False
    result = subprocess.run(["sudo", "-n", "-l", *command], capture_output=True, timeout=5)
    return result.returncode == 0
