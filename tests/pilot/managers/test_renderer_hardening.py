"""Renderers reject control chars as a last gate before writing unit files."""

import contextlib
from pathlib import Path

import pytest

from pilot.managers.processes.definitions import ProcessDefinition, hook_wrapped_argv
from pilot.managers.processes.supervisor import SupervisorRenderer
from pilot.managers.processes.systemd_render import SystemdRenderer


def test_systemd_render_rejects_newline_in_argv() -> None:
    pd = ProcessDefinition(
        name="mail-x",
        argv=["/bin/true\n[Service]\nExecStartPre=/x"],
        log_file=Path("/tmp/x.log"),
    )
    with pytest.raises(ValueError):
        SystemdRenderer("bench").render(pd)


def test_systemd_render_rejects_newline_in_env_value() -> None:
    pd = ProcessDefinition(
        name="mail-x",
        argv=["/bin/true"],
        log_file=Path("/tmp/x.log"),
        env={"K": "v\nExecStartPre=/x"},
    )
    with pytest.raises(ValueError):
        SystemdRenderer("bench").render(pd)


def test_supervisor_render_rejects_newline_in_argv() -> None:
    pd = ProcessDefinition(
        name="mail-x",
        argv=["/bin/true\ncommand=/x"],
        log_file=Path("/tmp/x.log"),
    )
    with pytest.raises(ValueError):
        SupervisorRenderer("bench", Path("/tmp")).render(pd)


def test_systemd_env_value_with_space_is_quoted() -> None:
    pd = ProcessDefinition(
        name="mail-x",
        argv=["/bin/true"],
        log_file=Path("/tmp/x.log"),
        env={"ARGS": "--flag one two"},
    )
    text = SystemdRenderer("bench").render(pd)
    # Quoted as one assignment, not split into stray tokens.
    assert 'Environment="ARGS=--flag one two"' in text


def test_supervisor_escapes_percent_and_quote() -> None:
    pd = ProcessDefinition(
        name="mail-x",
        argv=["/bin/foo", "--date=%Y"],
        log_file=Path("/tmp/x.log"),
        env={"MSG": 'a"b', "PCT": "50%"},
    )
    text = SupervisorRenderer("bench", Path("/tmp")).render(pd)
    assert "--date=%%Y" in text  # % doubled so supervisord won't expand it
    assert "%%" in text and "50%%" in text
    assert '\\"' in text  # embedded quote escaped


def _hooked(**kwargs) -> ProcessDefinition:
    return ProcessDefinition(
        name="mail-x",
        argv=["/bin/flow", "serve"],
        log_file=Path("/tmp/x.log"),
        **kwargs,
    )


def test_systemd_renders_hooks_and_restart() -> None:
    pd = _hooked(
        pre_run=["/bin/install.sh"],
        post_run=["/bin/cleanup.sh"],
        restart_on_failure=False,
    )
    text = SystemdRenderer("bench").render(pd)
    assert "ExecStartPre=/bin/install.sh\n" in text
    assert "ExecStopPost=/bin/cleanup.sh\n" in text
    assert "Restart=no\n" in text
    # The hook must not leak into the command the service actually runs.
    assert "ExecStart=/bin/flow serve\n" in text


def test_systemd_routes_bare_commands_through_env() -> None:
    """systemd rejects a relative ExecStart, so a bare name needs `env` to get
    the PATH lookup supervisor and the dev runner already do."""
    pd = ProcessDefinition(name="mail-x", argv=["flow", "serve"], log_file=Path("/tmp/x.log"))
    text = SystemdRenderer("bench").render(pd)
    assert "ExecStart=/usr/bin/env flow serve\n" in text


def test_systemd_leaves_absolute_commands_alone() -> None:
    assert "ExecStart=/bin/flow serve\n" in SystemdRenderer("bench").render(_hooked())


def test_systemd_defaults_to_restarting() -> None:
    assert "Restart=on-failure\n" in SystemdRenderer("bench").render(_hooked())


def test_systemd_render_rejects_newline_in_hook() -> None:
    pd = _hooked(pre_run=["/bin/x\n[Service]\nExecStartPre=/evil"])
    with pytest.raises(ValueError):
        SystemdRenderer("bench").render(pd)


def test_supervisor_wraps_hooks_and_honours_restart() -> None:
    pd = _hooked(pre_run=["/bin/install.sh"], post_run=["/bin/cleanup.sh"], restart_on_failure=False)
    text = SupervisorRenderer("bench", Path("/tmp")).render(pd)
    assert "command=sh -c " in text
    assert "/bin/install.sh && /bin/flow serve" in text
    assert "/bin/cleanup.sh" in text
    assert "autorestart=false\n" in text


def test_supervisor_restart_on_failure_maps_to_unexpected_not_any_exit() -> None:
    # 'true' would also restart a clean, expected (0) exit - only 'unexpected'
    # matches systemd's Restart=on-failure semantics.
    pd = _hooked(restart_on_failure=True)
    text = SupervisorRenderer("bench", Path("/tmp")).render(pd)
    assert "autorestart=unexpected\n" in text
    assert "exitcodes=0\n" in text


def test_hook_wrapping_is_skipped_without_hooks() -> None:
    pd = _hooked()
    assert hook_wrapped_argv(pd) == ["/bin/flow", "serve"]


def test_hook_wrapper_traps_post_run_on_exit_and_quotes_args() -> None:
    pd = _hooked(post_run=["/bin/cleanup.sh", "a b"])
    argv = hook_wrapped_argv(pd)
    assert argv[:2] == ["sh", "-c"]
    # post_run is wrapped in a function rather than inlined into the trap string,
    # since a post_run argv containing its own quotes (e.g. `bash -c '...'`) would
    # break a second layer of quoting around it.
    assert "trap _post_run EXIT" in argv[2]
    assert "'a b'" in argv[2]  # arg with a space stays one arg


def test_hook_wrapper_runs_post_run_when_group_is_signalled(tmp_path: Path) -> None:
    """An EXIT-only trap does not reliably fire when `sh` itself is killed by a
    signal while blocked waiting on a foreground child - only an explicit TERM
    trap runs before the shell's default disposition takes it down. This is the
    actual failure mode supervisor/the dev runner produce (SIGTERM to the whole
    process group via os.killpg), so exercise it for real rather than just
    asserting the wrapper script's text looks right."""
    import os
    import signal
    import subprocess
    import time

    marker = tmp_path / "stopped"
    pd = ProcessDefinition(
        name="mail-x",
        argv=["sleep", "999"],
        log_file=Path("/tmp/x.log"),
        post_run=["bash", "-c", f"echo stopped >> {marker}"],
    )
    proc = subprocess.Popen(hook_wrapped_argv(pd), preexec_fn=os.setsid)
    try:
        time.sleep(0.3)
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    assert marker.read_text() == "stopped\n"


def test_hook_wrapper_runs_post_run_exactly_once_on_signal(tmp_path: Path) -> None:
    """The EXIT trap re-fires after the TERM trap's handler returns; a guard
    must stop post_run running twice on a managed stop."""
    import os
    import signal
    import subprocess
    import time

    marker = tmp_path / "stopped"
    pd = ProcessDefinition(
        name="mail-x",
        argv=["sleep", "999"],
        log_file=Path("/tmp/x.log"),
        post_run=["bash", "-c", f"echo stopped >> {marker}"],
    )
    proc = subprocess.Popen(hook_wrapped_argv(pd), preexec_fn=os.setsid)
    try:
        time.sleep(0.3)
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        proc.wait(timeout=5)
    finally:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    assert marker.read_text().count("stopped") == 1
