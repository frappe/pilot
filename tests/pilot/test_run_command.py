"""Tests for pilot.utils.run_command - timeout, redaction, cancellation."""

from __future__ import annotations

import subprocess
import sys
import time
from unittest.mock import MagicMock, patch

import pytest

from pilot.exceptions import CommandError
from pilot.utils import run_command


def test_successful_command_returns_completed_process() -> None:
    result = run_command(["echo", "hello"])
    assert result.returncode == 0


def test_nonzero_exit_raises_command_error_with_returncode() -> None:
    with pytest.raises(CommandError) as excinfo:
        run_command(["sh", "-c", "exit 3"])
    assert excinfo.value.returncode == 3


def test_timeout_raises_command_error_mentioning_timeout() -> None:
    with pytest.raises(CommandError) as excinfo:
        run_command(["sleep", "5"], timeout=0.3)
    assert "timed out" in str(excinfo.value).lower()
    assert "0.3" in str(excinfo.value)


def test_timeout_actually_terminates_the_process_group() -> None:
    with pytest.raises(CommandError):
        run_command(["sh", "-c", "sleep 5"], timeout=0.3)
    time.sleep(0.2)
    result = subprocess.run(["pgrep", "-f", "sleep 5"], capture_output=True)
    assert result.stdout == b""


def test_redacted_secret_does_not_appear_in_error_message() -> None:
    secret = "my-super-secret-token"
    with pytest.raises(CommandError) as excinfo:
        run_command(
            ["sh", "-c", f"echo {secret} 1>&2; exit 1"],
            redactions=[secret],
        )
    message = str(excinfo.value)
    assert secret not in message
    assert "[redacted]" in message


def test_stream_output_still_returns_completed_process() -> None:
    result = run_command(["echo", "hello"], stream_output=True)
    assert result.returncode == 0
    assert result.stdout is None


def test_explicit_child_environment_keeps_task_launch_identity(monkeypatch) -> None:
    monkeypatch.setenv("BENCH_TASK_LAUNCH_ID", "task-launch")
    monkeypatch.setenv("PILOT_NONINTERACTIVE_PRIVILEGES", "1")
    process = MagicMock()

    with patch("pilot.utils.subprocess.Popen", return_value=process) as popen:
        from pilot.utils import _start_process

        assert _start_process(["true"], None, {"PATH": "/bin"}, False) is process

    assert popen.call_args.kwargs["env"]["BENCH_TASK_LAUNCH_ID"] == "task-launch"
    assert popen.call_args.kwargs["env"]["PILOT_NONINTERACTIVE_PRIVILEGES"] == "1"


def test_sudo_command_keeps_controlling_terminal() -> None:
    """sudo must not be setsid'd away from the tty it caches credentials against."""
    from pilot.utils import _start_process

    with patch("pilot.utils.subprocess.Popen", return_value=MagicMock()) as popen:
        _start_process(["sudo", "mkdir", "-p", "/opt/x"], None, None, False)

    assert popen.call_args.kwargs["start_new_session"] is False


def test_non_privileged_command_starts_new_session() -> None:
    from pilot.utils import _start_process

    with patch("pilot.utils.subprocess.Popen", return_value=MagicMock()) as popen:
        _start_process(["mkdir", "-p", "/opt/x"], None, None, False)

    assert popen.call_args.kwargs["start_new_session"] is True


def test_timeout_kills_a_child_that_does_not_lead_its_process_group(monkeypatch) -> None:
    from pilot import utils

    original_start = utils._start_process

    def start_attached(argv, cwd, env, stream_output, pipe_stdin=False):
        with patch("pilot.utils.subprocess.Popen", wraps=subprocess.Popen) as popen:

            def attached(*args, **kwargs):
                kwargs["start_new_session"] = False
                return popen(*args, **kwargs)

            with patch("pilot.utils.subprocess.Popen", side_effect=attached):
                return original_start(argv, cwd, env, stream_output, pipe_stdin)

    monkeypatch.setattr(utils, "_start_process", start_attached)
    started = time.monotonic()
    with pytest.raises(CommandError, match="timed out"):
        run_command([sys.executable, "-c", "import time; time.sleep(60)"], timeout=0.2)
    assert time.monotonic() - started < 3


def test_failure_reports_stdout_when_stderr_is_empty() -> None:
    """Frappe prints why it refused on stdout, so an exit code alone tells the caller
    nothing - the reason has to survive into the error."""
    with pytest.raises(CommandError) as excinfo:
        run_command(["sh", "-c", "echo 'App x depends on y.'; exit 1"])
    assert "App x depends on y." in str(excinfo.value)


def test_failure_prefers_stderr_over_stdout() -> None:
    with pytest.raises(CommandError) as excinfo:
        run_command(["sh", "-c", "echo noise; echo real-error 1>&2; exit 1"])
    assert "real-error" in str(excinfo.value)
    assert "noise" not in str(excinfo.value)
