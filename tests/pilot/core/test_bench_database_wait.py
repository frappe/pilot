from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.core.bench.runtime import BenchRuntime
from pilot.managers.database import MariaDBManager
from tests.pilot.commands.test_commands import make_bench


def _runtime(tmp_path: Path, production: bool = True, existing: bool = False) -> BenchRuntime:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = production
    bench.config.mariadb.existing = existing
    return BenchRuntime(bench)


@pytest.mark.parametrize("reachable", [True, False])
def test_a_production_bench_is_ready_when_its_own_database_is(tmp_path: Path, reachable: bool) -> None:
    with patch.object(MariaDBManager, "is_reachable", return_value=reachable):
        assert _runtime(tmp_path).is_database_ready is reachable


@pytest.mark.parametrize("production, existing", [(False, False), (True, True)])
def test_a_development_bench_or_an_existing_server_is_always_ready(
    tmp_path: Path, production: bool, existing: bool
) -> None:
    with patch.object(MariaDBManager, "is_reachable") as is_reachable:
        assert _runtime(tmp_path, production, existing).is_database_ready

    is_reachable.assert_not_called()


def test_a_restart_brings_nginx_in_line_with_the_sandbox_before_the_units_restart(tmp_path: Path) -> None:
    """`sandbox` takes effect on `pilot restart`, so nginx must start refusing links in sites/ there too."""
    from unittest.mock import MagicMock

    from pilot.managers.nginx import NginxManager
    from pilot.managers.processes.local import ProcessManager

    calls = MagicMock()
    manager = calls.manager
    manager.is_configured.return_value = True
    with (
        patch.object(ProcessManager, "for_bench", return_value=manager),
        patch.object(NginxManager, "reload_for_asset_change", calls.nginx),
    ):
        _runtime(tmp_path).restart_workload(include_admin=False, on_progress=lambda _message: None)

    order = [name for name, _args, _kwargs in calls.mock_calls]
    assert order.index("nginx") < order.index("manager.restart")
