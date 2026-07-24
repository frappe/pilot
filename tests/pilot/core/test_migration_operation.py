"""Tests for pilot.core.bench.migration - state transitions, operation lifecycle, and diagnosis."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.app import RevisionPin
from pilot.core.bench.migration.diagnosis import diagnose
from pilot.core.bench.migration.operation import AppRevision
from pilot.core.bench.migration.state import MigrationStateError, get_state, validate_transition
from pilot.exceptions import BenchError, MigrateError
from pilot.integrations.marketplace import Marketplace


def test_state_transitions() -> None:
    assert str(get_state("preparing")) == "preparing"
    assert get_state("preparing") == "preparing"

    # Valid transitions
    validate_transition("preparing", "backing_up")
    validate_transition("backing_up", "updating")
    validate_transition("updating", "migrating")
    validate_transition("migrating", "completed")
    validate_transition("migrating", "needs_attention")
    validate_transition("needs_attention", "retrying")
    validate_transition("retrying", "migrating")
    validate_transition("needs_attention", "reverting_apps")
    validate_transition("reverting_apps", "reverting_sites")
    validate_transition("reverting_sites", "restarting")
    validate_transition("restarting", "reverted")

    # Invalid transitions
    with pytest.raises(MigrationStateError, match="Illegal migration transition"):
        validate_transition("preparing", "completed")

    with pytest.raises(MigrationStateError, match="Illegal migration transition"):
        validate_transition("completed", "migrating")


def test_diagnosis_classification() -> None:
    output = "Executing frappe.patches.v14_0.update_to_v14 in site1\nIncorrect integer value: 'abc' for column 'status' at row 1"
    result = diagnose(output, "Migration error")

    assert result["phase"] == "migrate"
    assert result["patch"] == "frappe.patches.v14_0.update_to_v14"
    assert result["column"] == "status"
    assert result["failure_kind"] == "string_to_number"
    assert "Incorrect integer value" in result["output_excerpt"]


def test_diagnosis_unknown_failure() -> None:
    result = diagnose("Unexpected syntax error line 10", "General failure")

    assert result["patch"] is None
    assert result["failure_kind"] == "unknown"


def test_app_revision_builds_github_compare_url() -> None:
    revision = AppRevision(
        "frappe",
        "1111111",
        repository_url="git@github.com:frappe/frappe.git",
        updated_sha="2222222",
    )

    assert revision.compare_url == "https://github.com/frappe/frappe/compare/1111111...2222222"


def test_app_revision_omits_compare_url_for_non_github_repository() -> None:
    revision = AppRevision(
        "private_app",
        "1111111",
        repository_url="https://gitlab.example.com/team/private_app.git",
        updated_sha="2222222",
    )

    assert revision.compare_url is None


def test_app_revision_omits_compare_url_for_incomplete_github_repository() -> None:
    revision = AppRevision(
        "frappe",
        "1111111",
        repository_url="https://github.com/frappe",
        updated_sha="2222222",
    )

    assert revision.compare_url is None


def test_operation_creation_and_chaining(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench.apps.return_value = []
    mock_bench.sites.return_value = [MagicMock(config=MagicMock(name="site1.localhost"))]
    mock_bench.site.return_value.maintenance_settings = {
        "maintenance_mode": 0,
        "pause_scheduler": 1,
    }

    from pilot.core.bench.migration.store import MigrationStore

    store = MigrationStore(mock_bench)
    op = store.create_site_migrate("site1.localhost")

    assert op.kind == "site_migrate"
    assert op.state == "preparing"
    assert len(op.sites) == 1
    assert op.sites[0].name == "site1.localhost"
    assert op.sites[0].backup_status == "pending"
    assert op.sites[0].migration_status == "pending"

    # Test begin() queues first backup task
    with patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-101"):
        task_id = op.begin()

    assert task_id == "task-101"
    assert op.state == "backing_up"
    assert len(op.chain) == 1
    assert op.chain[0]["command"] == "migration-backup"
    assert op.chain[0]["site"] == "site1.localhost"
    mock_bench.site.return_value.set_maintenance_mode.assert_called_once_with(True)
    assert op.sites[0].original_config == {
        "maintenance_mode": 0,
        "pause_scheduler": 1,
    }


def test_create_rejects_new_operation_while_one_is_unresolved(tmp_path: Path) -> None:
    from pilot.core.bench.migration.store import MigrationStore
    from pilot.exceptions import MigrationConflictError

    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench.apps.return_value = []
    mock_bench.sites.return_value = [MagicMock(config=MagicMock(name="site1.localhost"))]

    store = MigrationStore(mock_bench)
    store.create_site_migrate("site1.localhost")

    with pytest.raises(MigrationConflictError):
        store.create_site_migrate("site1.localhost")


def test_create_allowed_again_once_previous_operation_is_resolved(tmp_path: Path) -> None:
    from pilot.core.bench.migration.store import MigrationStore

    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench.apps.return_value = []
    mock_bench.sites.return_value = [MagicMock(config=MagicMock(name="site1.localhost"))]

    store = MigrationStore(mock_bench)
    first = store.create_site_migrate("site1.localhost")
    first.state = get_state("completed")
    store.save(first)

    second = store.create_site_migrate("site1.localhost")
    assert second.id != first.id


def test_current_prefers_failed_operation_over_active_run(tmp_path: Path) -> None:
    from pilot.core.bench.migration.operation import MigrationOperation
    from pilot.core.bench.migration.store import MigrationStore

    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    store = MigrationStore(mock_bench)

    def make_op(op_id: str, state: str) -> MigrationOperation:
        operation = MigrationOperation(
            id=op_id,
            kind="update",
            state=get_state(state),
            created_at="2026-01-01T00:00:00+00:00",
            started_at=None,
            finished_at=None,
            apps=[],
            apps_filter=None,
            sites=[],
        )
        operation.bench = mock_bench
        operation.store = store
        store.save(operation)
        return operation

    failed = make_op("20260101-000001-aaaa", "needs_attention")  # older
    active = make_op("20260101-000002-bbbb", "migrating")  # newer

    assert store.current().id == failed.id

    failed.state = get_state("completed")
    store.save(failed)
    assert store.current().id == active.id

    active.state = get_state("completed")
    store.save(active)
    assert store.current() is None


def test_update_operation_records_app_repository(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    app = MagicMock()
    app.config.name = "frappe"
    app.config.repo = "https://github.com/frappe/frappe.git"
    app.installed_hash = "1111111"
    app.update_target.return_value = RevisionPin(kind="commit", ref="2222222")
    mock_bench.apps.return_value = [app]
    mock_bench.sites.return_value = []

    from pilot.core.bench.migration.store import MigrationStore

    with patch.object(Marketplace, "registry", return_value=[]):
        operation = MigrationStore(mock_bench).create_update()

    assert operation.apps == [
        AppRevision(
            "frappe",
            "1111111",
            repository_url="https://github.com/frappe/frappe.git",
            target_sha="2222222",
        )
    ]


def test_update_operation_captures_tag_pinned_target(tmp_path: Path) -> None:
    """A marketplace tag target must also be captured at create time, not just commits."""
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    app = MagicMock()
    app.config.name = "helpdesk"
    app.config.repo = "https://github.com/frappe/helpdesk.git"
    app.installed_hash = "aaaaaaa"
    app.update_target.return_value = RevisionPin(kind="tag", ref="v2.0.0")
    mock_bench.apps.return_value = [app]
    mock_bench.sites.return_value = []

    from pilot.core.bench.migration.store import MigrationStore

    with patch.object(Marketplace, "registry", return_value=[]):
        operation = MigrationStore(mock_bench).create_update()

    assert operation.apps == [
        AppRevision(
            "helpdesk",
            "aaaaaaa",
            repository_url="https://github.com/frappe/helpdesk.git",
            target_sha="v2.0.0",
            target_kind="tag",
        )
    ]


def _site_with_apps(name: str, apps: list[str]) -> MagicMock:
    site = MagicMock()
    site.config.name = name
    site.installed_apps.return_value = apps
    return site


def test_create_update_only_includes_sites_with_a_selected_app(tmp_path: Path) -> None:
    """Updating 'hrms' shouldn't back up/migrate a site that doesn't have it installed."""
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    hrms = MagicMock()
    hrms.config.name = "hrms"
    hrms.config.repo = ""
    hrms.installed_hash = "1111111"
    hrms.update_target.return_value = None
    mock_bench.apps.return_value = [hrms]
    mock_bench.sites.return_value = [
        _site_with_apps("with-hrms.local", ["frappe", "hrms"]),
        _site_with_apps("without-hrms.local", ["frappe", "erpnext"]),
    ]

    from pilot.core.bench.migration.store import MigrationStore

    with patch.object(Marketplace, "registry", return_value=[]):
        operation = MigrationStore(mock_bench).create_update({"hrms"})

    assert [site.name for site in operation.sites] == ["with-hrms.local"]


def test_create_update_includes_a_site_when_its_apps_cannot_be_determined(tmp_path: Path) -> None:
    """A site whose installed-apps lookup fails (empty result) is included, not silently skipped."""
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    hrms = MagicMock()
    hrms.config.name = "hrms"
    hrms.config.repo = ""
    hrms.installed_hash = "1111111"
    hrms.update_target.return_value = None
    mock_bench.apps.return_value = [hrms]
    mock_bench.sites.return_value = [_site_with_apps("unknown.local", [])]

    from pilot.core.bench.migration.store import MigrationStore

    with patch.object(Marketplace, "registry", return_value=[]):
        operation = MigrationStore(mock_bench).create_update({"hrms"})

    assert [site.name for site in operation.sites] == ["unknown.local"]


def test_operation_site_lifecycle(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    site_dir = tmp_path / "sites" / "site1.localhost"
    site_dir.mkdir(parents=True)
    site_mock = MagicMock()
    site_mock.path = site_dir
    site_mock.maintenance_settings = {"maintenance_mode": 0, "pause_scheduler": 1}
    site_mock.migration_backup.create.return_value = ["tabUser", "tabDocType"]
    mock_bench.site.return_value = site_mock

    from pilot.core.bench.migration.store import MigrationStore

    store = MigrationStore(mock_bench)
    op = store.create_site_migrate("site1.localhost")
    with patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-101"):
        op.begin()

    # Site backup step
    op.back_up_site("site1.localhost")
    assert op.sites[0].backup_status == "backed_up"
    site_mock.migration_backup.create.assert_called_once_with(op.id)
    assert op.state == "migrating"

    # Site migrate step
    op.migrate_site("site1.localhost")
    assert op.sites[0].migration_status == "success"
    assert op.state == "completed"
    assert op.finished_at is not None
    site_mock.set_maintenance_settings.assert_called_with(
        {"maintenance_mode": 0, "pause_scheduler": 1}
    )


def test_operation_failure_and_retry_rearm(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    site_dir = tmp_path / "sites" / "site1.localhost"
    site_dir.mkdir(parents=True)
    site_mock = MagicMock()
    site_mock.path = site_dir
    site_mock.maintenance_mode = False
    site_mock.migrate.side_effect = MigrateError("Patch failure in v15", output="Executing patch_x in site1\nError")
    mock_bench.site.return_value = site_mock

    from pilot.core.bench.migration.store import MigrationStore

    store = MigrationStore(mock_bench)
    op = store.create_site_migrate("site1.localhost")
    op.state = get_state("migrating")


    with pytest.raises(MigrateError):
        op.migrate_site("site1.localhost")

    assert op.sites[0].migration_status == "failed"
    assert op.state == "needs_attention"
    assert op.failed_site == "site1.localhost"
    assert op.diagnosis["patch"] == "patch_x"
    assert op.sites[0].touched_tables_trusted is False

    # Test retry re-arm
    op.retry_arm()
    assert op.state == "migrating"
    assert op.sites[0].migration_status == "pending"
    assert op.failed_site is None


def test_chain_hands_resources_off_from_current_task(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.state = get_state("backing_up")
    operation.chain.append(
        {"command": "migration-backup", "task_id": "task-101", "site": "site1.localhost"}
    )

    with patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-102") as queue:
        operation.enqueue_next(handoff_from="task-101")

    assert queue.call_args.kwargs["resource_handoff_from"] == "task-101"


def test_update_records_the_new_app_revision(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench.app.return_value.installed_hash = "2222222"

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench)._create(
        "update",
        apps=[
            AppRevision(
                "frappe",
                "1111111",
                repository_url="https://github.com/frappe/frappe.git",
            )
        ],
        apps_filter=None,
        sites=[],
    )
    operation.state = get_state("updating")

    operation.update_apps()

    assert operation.apps[0].updated_sha == "2222222"
    assert operation.apps[0].compare_url == (
        "https://github.com/frappe/frappe/compare/1111111...2222222"
    )
    # No sites to migrate - the migrating phase has nothing to wait on, so it
    # must complete immediately instead of getting stuck.
    assert operation.state == "completed"


def test_update_apps_checks_out_the_pin_captured_at_create_time(tmp_path: Path) -> None:
    """update_apps() must deploy exactly what was captured at create time, never
    re-resolve a marketplace/branch target that may have moved on since."""
    from pilot.core.app import RevisionPin

    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench.app.return_value.installed_hash = "9999999"

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench)._create(
        "update",
        apps=[
            AppRevision("frappe", "1111111", target_sha="2222222", target_kind="commit"),
            AppRevision("helpdesk", "aaaaaaa", target_sha="v2.0.0", target_kind="tag"),
        ],
        apps_filter=None,
        sites=[],
    )
    operation.state = get_state("updating")

    operation.update_apps()

    args, _kwargs = mock_bench._update_apps.call_args
    assert args[0] is None
    assert args[2] == {
        "frappe": RevisionPin(kind="commit", ref="2222222"),
        "helpdesk": RevisionPin(kind="tag", ref="v2.0.0"),
    }


def test_backup_failure_restores_every_sites_original_settings(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    sites = {}
    for name, scheduler in (("one.local", 1), ("two.local", 0)):
        site = MagicMock()
        site.maintenance_settings = {"maintenance_mode": 0, "pause_scheduler": scheduler}
        site.migration_backup.create.return_value = []
        sites[name] = site
    sites["two.local"].migration_backup.create.side_effect = RuntimeError("dump failed")
    mock_bench.site.side_effect = sites.__getitem__

    from pilot.core.bench.migration.operation import AppRevision
    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench)._create(
        "update",
        apps=[AppRevision("frappe", "abc")],
        apps_filter=None,
        sites=list(sites),
    )
    with patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-101"):
        operation.begin()

    operation.back_up_site("one.local")
    with pytest.raises(RuntimeError, match="dump failed"):
        operation.back_up_site("two.local")

    for name, scheduler in (("one.local", 1), ("two.local", 0)):
        sites[name].set_maintenance_settings.assert_called_with(
            {"maintenance_mode": 0, "pause_scheduler": scheduler}
        )


def test_bypass_patch_rejects_patch_other_than_diagnosed_patch(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.state = get_state("needs_attention")
    operation.failed_site = "site1.localhost"
    operation.diagnosis = {"patch": "frappe.patches.expected"}

    with (
        patch("pilot.utils.run_command") as run_command,
        pytest.raises(MigrationStateError, match="diagnosed failing patch"),
    ):
        operation.bypass_patch("frappe.patches.other")

    run_command.assert_not_called()


def test_corrupt_operation_record_fails_loudly(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path

    from pilot.core.bench.migration.store import MigrationStore

    store = MigrationStore(mock_bench)
    store.root.mkdir()
    (store.root / "broken.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(BenchError, match="Could not load migration operation"):
        store.get_all()


def test_restore_uses_full_fallback_when_touched_tables_are_untrusted(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    site_mock = MagicMock()
    mock_bench.site.return_value = site_mock

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.state = get_state("needs_attention")
    operation.sites[0].backup_status = "backed_up"
    operation.sites[0].migration_status = "failed"
    operation.sites[0].touched_tables = ["tabUser"]
    operation.sites[0].touched_tables_trusted = False

    operation.revert_arm()
    operation.revert_site(operation.sites[0].name)

    site_mock.migration_backup.restore.assert_called_once_with([])
    site_mock.clear_cache.assert_called_once()
    assert operation.sites[0].migration_status == "recovered"


def test_revert_site_marks_recovering_before_restore(tmp_path: Path) -> None:
    """The site status should flip to 'recovering' while restore/clear_cache are in flight."""
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    site_mock = MagicMock()
    mock_bench.site.return_value = site_mock

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.state = get_state("needs_attention")
    operation.sites[0].backup_status = "backed_up"
    operation.sites[0].migration_status = "failed"

    def assert_recovering_mid_restore(_tables: list[str]) -> None:
        assert operation.sites[0].migration_status == "recovering"

    site_mock.migration_backup.restore.side_effect = assert_recovering_mid_restore

    operation.revert_arm()
    operation.revert_site(operation.sites[0].name)

    assert operation.sites[0].migration_status == "recovered"


def test_revert_skips_reverting_apps_phase_when_no_apps(tmp_path: Path) -> None:
    """A standalone site migration has no apps, so restore should arm straight into site recovery."""
    mock_bench = MagicMock()
    mock_bench.path = tmp_path

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.state = get_state("needs_attention")
    operation.sites[0].backup_status = "backed_up"
    operation.sites[0].migration_status = "failed"

    operation.revert_arm()

    assert operation.state == "reverting_sites"


def test_next_revert_site_skips_pending_and_recovered_sites(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.sites[0].migration_status = "pending"

    assert operation.next_revert_site() is None

    operation.sites[0].migration_status = "success"
    assert operation.next_revert_site() == "site1.localhost"

    operation.revert_checkpoints["site:site1.localhost"] = True
    assert operation.next_revert_site() is None


def test_snapshot_creation_rejects_another_unresolved_owner(tmp_path: Path) -> None:
    from pilot.core.site.migration_backup import SiteMigrationBackup

    site = MagicMock()
    site.path = tmp_path / "site1.localhost"
    site.config.name = "site1.localhost"
    site.bench.migrations.unresolved_for_site.return_value = [MagicMock(id="other-operation")]

    with pytest.raises(BenchError, match="another unresolved operation"):
        SiteMigrationBackup(site).create("current-operation")


def test_bypass_patch_audit_failure_is_not_hidden(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench.frappe_call = ["bench"]
    mock_bench.sites_path = tmp_path / "sites"

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.state = get_state("needs_attention")
    operation.failed_site = "site1.localhost"
    operation.diagnosis = {"patch": "frappe.patches.expected"}

    with (
        patch("pilot.utils.run_command", return_value=MagicMock(returncode=0)),
        patch("pilot.core.bench.audit_log.AuditLog.append", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        operation.bypass_patch("frappe.patches.expected")

    assert operation.decisions[-1]["patch"] == "frappe.patches.expected"
