from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.core.site.commands import SiteCommands
from pilot.tasks.complete_setup import CompleteSetupTask


def test_the_task_passes_only_the_given_answers_in_frappe_names(tmp_path: Path) -> None:
    bench = MagicMock()
    task = CompleteSetupTask(
        bench=bench,
        bench_root=tmp_path,
        site="site.local",
        full_name="Asha Rao",
        email="asha@example.com",
        time_zone="UTC",
    )

    with patch.object(task, "step", MagicMock()):
        task.complete_setup()

    bench.site.return_value.complete_setup.assert_called_once_with(
        {"full_name": "Asha Rao", "email": "asha@example.com", "timezone": "UTC"}
    )


def test_setup_runs_frappe_setup_complete_with_the_answers() -> None:
    site = MagicMock()
    site.config.name = "site.local"
    site._frappe_call.side_effect = lambda *args: ["frappe-call", *args]

    with patch("pilot.core.site.commands.run_command", return_value=MagicMock(returncode=0)) as run:
        SiteCommands(site).complete_setup({"email": "asha@example.com"})

    command = run.call_args.args[0]
    assert command[:6] == [
        "frappe-call",
        "frappe",
        "--site",
        "site.local",
        "execute",
        "frappe.desk.page.setup_wizard.setup_wizard.setup_complete",
    ]
    assert json.loads(command[-1]) == {"args": {"email": "asha@example.com"}}
