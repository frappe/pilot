from __future__ import annotations

import getpass
from pathlib import Path

from bench_cli.managers.process_manager import ProcessDefinition
from bench_cli.managers.process_managers.openrc import OpenRCRenderer


def _pd(name: str = "web", command: str = "/usr/bin/gunicorn frappe.app") -> ProcessDefinition:
    return ProcessDefinition(name=name, command=command, log_file=Path("/x/web.log"))


def test_render_drops_to_bench_user() -> None:
    """supervise-daemon runs as root, so the rendered script must set
    command_user to the bench user, matching the systemd (--user) and
    supervisor backends, which never run the workload as root."""
    script = OpenRCRenderer("b1", getpass.getuser()).render(_pd())
    assert f'command_user="{getpass.getuser()}"' in script
    assert "supervisor=supervise-daemon" in script


def test_render_includes_working_dir_and_env() -> None:
    pd = ProcessDefinition(
        name="web",
        command="/usr/bin/gunicorn frappe.app:application",
        log_file=Path("/x/web.log"),
        env={"MALLOC_ARENA_MAX": "2"},
        working_dir=Path("/sites"),
    )
    script = OpenRCRenderer("b1", "frappe").render(pd)
    assert 'directory="/sites"' in script
    assert 'export MALLOC_ARENA_MAX="2"' in script
    assert 'command="/usr/bin/gunicorn"' in script
    assert 'command_args="frappe.app:application"' in script
