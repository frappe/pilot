from __future__ import annotations

import getpass
from pathlib import Path
from types import SimpleNamespace

from pilot.managers.openrc_process_manager import OpenRCProcessManager
from pilot.managers.process_manager import ProcessDefinition


def _manager() -> OpenRCProcessManager:
    mgr = OpenRCProcessManager.__new__(OpenRCProcessManager)
    mgr.bench = SimpleNamespace(config=SimpleNamespace(name="b1"))  # type: ignore[assignment]
    return mgr


def _pd(name: str = "web", command: str = "/usr/bin/gunicorn frappe.app") -> ProcessDefinition:
    return ProcessDefinition(name=name, command=command, log_file=Path("/x/web.log"))


def test_render_service_drops_to_bench_user() -> None:
    """supervise-daemon runs as root, so the rendered script must set
    command_user to the bench user — matching the systemd (--user) and
    supervisor backends, which never run the workload as root."""
    script = _manager()._render_service(_pd())
    assert f'command_user="{getpass.getuser()}"' in script
    assert "supervisor=supervise-daemon" in script
