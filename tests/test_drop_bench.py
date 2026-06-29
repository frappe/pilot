"""Engine-aware database teardown in DropBenchCommand."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from pilot.commands.drop_bench import DropBenchCommand
from pilot.config.postgres_config import PostgresConfig


def _cmd(instance: str = "b1", path: Path | None = None) -> DropBenchCommand:
    bench = SimpleNamespace(
        path=path or Path("/tmp/benches/b1"),
        config=SimpleNamespace(
            db_type="postgres",
            postgres=PostgresConfig(instance=instance, host="localhost", port=5433),
        ),
    )
    return DropBenchCommand(bench)


def test_teardown_removes_dedicated_cluster() -> None:
    cmd = _cmd()
    with patch.object(cmd, "_postgres_shared_with_other_bench", return_value=False), \
         patch.object(cmd, "_remove_postgres_instance") as remove:
        cmd._teardown_postgres()
    remove.assert_called_once()


def test_teardown_keeps_cluster_shared_with_sibling() -> None:
    cmd = _cmd()
    with patch.object(cmd, "_postgres_shared_with_other_bench", return_value=True), \
         patch.object(cmd, "_remove_postgres_instance") as remove:
        cmd._teardown_postgres()
    remove.assert_not_called()


def test_teardown_noop_on_shared_server() -> None:
    cmd = _cmd(instance="")
    with patch.object(cmd, "_remove_postgres_instance") as remove:
        cmd._teardown_postgres()
    remove.assert_not_called()


def test_remove_postgres_instance_calls_manager() -> None:
    cmd = _cmd()
    with patch("pilot.managers.postgres_manager.PostgresManager") as manager:
        cmd._remove_postgres_instance()
    manager.return_value.remove_instance.assert_called_once()


def test_shared_detection_matches_sibling_cluster() -> None:
    cmd = _cmd(path=Path("/tmp/benches/b1"))
    sibling = SimpleNamespace(postgres=PostgresConfig(instance="b1", port=5433))
    with patch("pilot.utils.iter_sibling_benches", return_value=[("b2", sibling)]):
        assert cmd._postgres_shared_with_other_bench() is True


def test_shared_detection_ignores_unrelated_sibling() -> None:
    cmd = _cmd(instance="b1", path=Path("/tmp/benches/b1"))
    sibling = SimpleNamespace(postgres=PostgresConfig(instance="other", port=9999))
    with patch("pilot.utils.iter_sibling_benches", return_value=[("b2", sibling)]):
        assert cmd._postgres_shared_with_other_bench() is False
