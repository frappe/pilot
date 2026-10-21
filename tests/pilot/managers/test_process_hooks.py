from __future__ import annotations

import os
import shlex
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

from pilot.exceptions import BenchError
from pilot.managers.processes.definitions import ProcessDefinition, check_unique_names
from pilot.managers.processes.supervisor import SupervisorRenderer
from pilot.managers.processes.systemd_render import SystemdRenderer


def _definition(argv: list[str] | None = None, **kwargs) -> ProcessDefinition:
    return ProcessDefinition(
        name="mail-stalwart", argv=argv or ["/bin/stalwart"], log_file=Path("/tmp/x.log"), **kwargs
    )


def test_systemd_renders_hooks_and_restart() -> None:
    unit = SystemdRenderer("bench").render(
        _definition(pre_run=["install.sh"], post_run=["/bin/rm", "-f", "s.sock"], restart_on_failure=False)
    )

    assert "ExecStartPre=/usr/bin/env install.sh\n" in unit
    assert "ExecStart=/bin/stalwart\n" in unit
    assert "ExecStopPost=/bin/rm -f s.sock\n" in unit
    assert "Restart=no\n" in unit


def test_systemd_escapes_specifiers_and_quotes() -> None:
    unit = SystemdRenderer("bench").render(
        _definition(argv=["/bin/stalwart", "--rate=100%", "$HOME"], env={"MESSAGE": 'say "50%"'})
    )

    assert 'ExecStart=/bin/stalwart --rate=100%% "$$HOME"\n' in unit
    assert 'Environment="MESSAGE=say \\"50%%\\""\n' in unit


def test_supervisor_wraps_hooks_and_escapes_percent() -> None:
    program = SupervisorRenderer("bench", Path("/tmp")).render(
        _definition(
            argv=["/bin/stalwart", "--rate=100%"], pre_run=["/bin/install.sh"], env={"MESSAGE": 'say "50%" now'}
        )
    )

    assert "command=sh -c '/bin/install.sh || exit; exec /bin/stalwart --rate=100%%'\n" in program
    assert "environment=MESSAGE='say \"50%%\" now'\n" in program
    assert "autorestart=unexpected\n" in program


def test_definition_without_hooks_runs_directly() -> None:
    assert _definition().argv_with_hooks == ["/bin/stalwart"]


def test_post_run_waits_for_a_terminated_process(tmp_path: Path) -> None:
    """Supervisor and the dev runner send TERM to the whole group; post_run must see the process gone."""
    marker = tmp_path / "running"
    process = [
        sys.executable,
        "-c",
        "import os, signal, sys, time\n"
        f"signal.signal(signal.SIGTERM, lambda *_: (time.sleep(0.5), os.remove({str(marker)!r}), sys.exit(0)))\n"
        f"open({str(marker)!r}, 'w').close()\n"
        "time.sleep(60)",
    ]
    check = f"test -e {marker} && echo running > {tmp_path}/result || echo stopped > {tmp_path}/result"
    definition = _definition(argv=process, post_run=["sh", "-c", check])

    wrapper = subprocess.Popen(definition.argv_with_hooks, start_new_session=True)
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    assert marker.exists()
    os.killpg(wrapper.pid, signal.SIGTERM)

    assert wrapper.wait(timeout=5) == 0
    assert (tmp_path / "result").read_text() == "stopped\n"


def test_wrapper_keeps_the_process_exit_code(tmp_path: Path) -> None:
    definition = _definition(argv=["sh", "-c", "exit 7"], post_run=["touch", str(tmp_path / "cleaned")])

    assert subprocess.run(definition.argv_with_hooks).returncode == 7
    assert (tmp_path / "cleaned").exists()


def test_post_run_runs_when_pre_run_fails(tmp_path: Path) -> None:
    definition = _definition(pre_run=["sh", "-c", "exit 3"], post_run=["touch", str(tmp_path / "cleaned")])

    assert subprocess.run(definition.argv_with_hooks).returncode == 3
    assert (tmp_path / "cleaned").exists()


def test_hooked_command_is_a_single_line() -> None:
    definition = _definition(pre_run=["install.sh"], post_run=["cleanup.sh"])

    assert "\n" not in definition.argv_with_hooks[2]


def test_systemd_web_listens_through_a_socket_a_restart_leaves_open() -> None:
    renderer = SystemdRenderer("bench")
    web = ProcessDefinition(
        name="web", argv=["/bin/gunicorn"], log_file=Path("/tmp/x.log"), listen_address="127.0.0.1:8000"
    )

    socket = renderer.render_socket(web)

    assert "Requires=bench-web.socket\nAfter=bench-web.socket\n" in renderer.render(web)
    assert "ListenStream=127.0.0.1:8000\n" in socket
    assert "StopPropagatedFrom=bench.target\n" in socket  # pilot stop stops it
    assert "PartOf" not in socket


def test_stop_during_pre_run_skips_the_process(tmp_path: Path) -> None:
    started = tmp_path / "started"
    definition = _definition(
        argv=["touch", str(started)],
        pre_run=["sh", "-c", f"trap 'exit 0' TERM; touch {tmp_path}/pre; sleep 5 & wait"],
        post_run=["touch", str(tmp_path / "cleaned")],
    )

    wrapper = subprocess.Popen(definition.argv_with_hooks, start_new_session=True)
    deadline = time.monotonic() + 5
    while not (tmp_path / "pre").exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    os.killpg(wrapper.pid, signal.SIGTERM)

    assert wrapper.wait(timeout=5) == 0
    assert not started.exists()
    assert (tmp_path / "cleaned").exists()


def test_supervisor_keeps_a_semicolon_or_hash_after_whitespace(tmp_path: Path) -> None:
    """supervisord reads ` ;` and ` #` as a comment, even inside quotes."""
    program = SupervisorRenderer("bench", Path("/tmp")).render(
        _definition(argv=["sh", "-c", "echo one ; echo two #three"])
    )

    command = program.split("command=", 1)[1].split("\n", 1)[0]
    assert command == "sh -c 'echo one '\\;' echo two '\\#'three'"
    assert shlex.split(command) == ["sh", "-c", "echo one ; echo two #three"]


# supervisord strips every quote from both ends of a value, so "50%" would arrive as 50%.
@pytest.mark.parametrize("value", ["a ;b", "say \"it's\"", '"50%"', "it's'"])
def test_supervisor_rejects_an_env_value_it_cannot_hold(value) -> None:
    with pytest.raises(BenchError, match="Supervisor cannot hold the value of MESSAGE"):
        SupervisorRenderer("bench", Path("/tmp")).render(_definition(env={"MESSAGE": value}))


def test_names_that_supervisor_would_merge_are_rejected() -> None:
    definitions = [
        ProcessDefinition(name="redis_cache", argv=["redis-server"], log_file=Path("/tmp/x.log")),
        _definition(),
        ProcessDefinition(name="redis-cache", argv=["/bin/cache"], log_file=Path("/tmp/x.log"), app="redis"),
    ]

    with pytest.raises(BenchError, match="Process 'redis-cache' of app 'redis' has the same name as 'redis_cache'"):
        check_unique_names(definitions)


def test_systemd_keeps_backslashes_and_quotes_in_hook_arguments() -> None:
    """systemd reads C escapes inside quotes, so a literal \\n must reach the hook as written."""
    unit = SystemdRenderer("bench").render(
        _definition(pre_run=["/usr/bin/python3", "-c", "print('a\\nb')", 'say "hi"', ""])
    )

    assert "ExecStartPre=/usr/bin/python3 -c \"print('a\\\\nb')\" \"say \\\"hi\\\"\" \"\"\n" in unit
