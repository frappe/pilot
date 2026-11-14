from __future__ import annotations

import contextlib
import io
import os
import signal
import subprocess
import sys
from pathlib import Path

from pilot.exceptions import CommandError


class Command:
    """One external command, run to completion in its own process group.

    A timeout or Ctrl-C kills the whole group, so no child outlives the command. A non-zero
    exit raises CommandError with the last useful line of output, secrets redacted."""

    # Task runners set these; a child command must see them to report back to its task.
    inherited_env_keys = ("BENCH_TASK_LAUNCH_ID", "PILOT_NONINTERACTIVE_PRIVILEGES")

    def __init__(
        self,
        argv: list[str],
        cwd: Path | None = None,
        env: dict | None = None,
        stream_output: bool = False,
        timeout: float | None = None,
        redactions: list[str] | None = None,
        stdin_text: str | None = None,
    ) -> None:
        self.argv = argv
        self.cwd = cwd
        self.env = env
        self.stream_output = stream_output
        self.timeout = timeout
        self.redactions = redactions
        self.stdin_text = stdin_text

    def run(self) -> subprocess.CompletedProcess:
        """Run the command and return its captured output; raise CommandError on failure."""
        process = self.start()
        stdout, stderr = self.wait(process)
        if process.returncode != 0:
            raise self.get_failure(process.returncode, stdout, stderr)
        return subprocess.CompletedProcess(self.argv, process.returncode, stdout, stderr)

    def run_with_tee(self) -> subprocess.CompletedProcess:
        """Print combined output live while also capturing it, for callers that classify it later."""
        if self.timeout is not None:
            raise ValueError("tee_output does not support timeout")
        process = subprocess.Popen(
            self.argv,
            cwd=self.cwd,
            env=self.child_env,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            start_new_session=True,
            text=True,
        )
        if not isinstance(process.stdout, io.TextIOWrapper):
            raise RuntimeError("Command output pipe was not created")
        process.stdout.reconfigure(newline="")
        captured: list[str] = []
        try:
            for line in process.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                captured.append(line)
        except KeyboardInterrupt:
            self.kill(process)
            raise
        process.wait()
        return subprocess.CompletedProcess(self.argv, process.returncode, "".join(captured), "")

    @property
    def child_env(self) -> dict | None:
        """The env for the child: the caller's env plus the task keys this process holds."""
        inherited = {key: os.environ[key] for key in self.inherited_env_keys if key in os.environ}
        if self.env is not None and inherited:
            return {**self.env, **inherited}
        return self.env

    @property
    def is_sudo(self) -> bool:
        """Whether the command runs through sudo, which must stay in the caller's session."""
        return bool(self.argv) and (self.argv[0] == "sudo" or self.argv[0].endswith("/sudo"))

    def start(self) -> subprocess.Popen:
        """Start the process; output is piped unless the caller wants it streamed."""
        return subprocess.Popen(
            self.argv,
            cwd=self.cwd,
            env=self.child_env,
            stdin=subprocess.PIPE if self.stdin_text is not None else None,
            stdout=None if self.stream_output else subprocess.PIPE,
            stderr=None if self.stream_output else subprocess.PIPE,
            start_new_session=not self.is_sudo,
        )

    def wait(self, process: subprocess.Popen) -> tuple[bytes, bytes]:
        """Wait for the process to end; kill it and raise when the timeout passes."""
        stdin_bytes = self.stdin_text.encode() if self.stdin_text is not None else None
        try:
            return process.communicate(input=stdin_bytes, timeout=self.timeout)
        except subprocess.TimeoutExpired as exc:
            self.kill(process)
            raise CommandError(
                f"Command {self.argv[0]!r} timed out after {self.timeout}s and was terminated.", returncode=-1
            ) from exc
        except KeyboardInterrupt:
            self.kill(process)
            raise

    def get_failure(self, returncode: int, stdout: bytes | None, stderr: bytes | None) -> CommandError:
        """The error to raise for a failed run, with the most useful output line."""
        detail = self.redact(stderr.decode(), self.redactions) if not self.stream_output and stderr else ""
        if not detail and not self.stream_output and stdout:
            # Frappe reports a refusal by throwing, which prints the reason - not a stack
            # frame - as the last line of stdout. Without it the caller sees only a code.
            detail = self.redact(stdout.decode(), self.redactions).strip().rsplit("\n", 1)[-1]
        return CommandError(
            f"Command {self.argv[0]!r} failed with exit code {returncode}.\n{detail}".strip(),
            returncode=returncode,
        )

    @staticmethod
    def kill(process: subprocess.Popen) -> None:
        """Kill the process and its group, then reap it."""
        # sudo keeps the caller's session; its pid is not a process-group id.
        with contextlib.suppress(ProcessLookupError):
            if os.getpgid(process.pid) == process.pid:
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        process.wait()

    @staticmethod
    def redact(text: str, secrets: list[str] | None) -> str:
        """The text with every secret replaced by [redacted], longest secret first."""
        if not text or not secrets:
            return text
        for secret in sorted(filter(None, secrets), key=len, reverse=True):
            text = text.replace(secret, "[redacted]")
        return text


def run_command(
    argv: list[str],
    cwd: Path | None = None,
    env: dict | None = None,
    stream_output: bool = False,
    timeout: float | None = None,
    redactions: list[str] | None = None,
    tee_output: bool = False,
    stdin_text: str | None = None,
) -> subprocess.CompletedProcess:
    """Run a command and return its output. The shortcut most callers use for Command."""
    command = Command(argv, cwd, env, stream_output, timeout, redactions, stdin_text)
    return command.run_with_tee() if tee_output else command.run()
