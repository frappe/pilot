from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.core.bench.audit_log import AuditLog
from pilot.tasks.drop_site import DropSiteTask
from tests.pilot.commands.test_commands import make_bench


def test_queue_audits_whether_the_drop_skips_its_backup(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    with patch.object(type(bench.tasks), "run_task", lambda self, *a, **k: "task-123"):
        DropSiteTask.queue(bench, site="site1.localhost", no_backup=True)

    queued = AuditLog(bench).entries(entry_type="task")
    assert queued[0]["command"] == "drop-site"
    assert queued[0]["args"] == {"site": "site1.localhost", "no_backup": True}
