"""Tests for pilot.core.bench.migration - state transitions, operation lifecycle, and diagnosis."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock, call, patch

import pytest

from pilot.core.app import RevisionPin
from pilot.core.bench.migration.diagnosis import diagnose
from pilot.core.bench.migration.operation import AppRevision, SiteProgress
from pilot.core.bench.migration.state import MigrationStateError, get_state, validate_transition
from pilot.exceptions import BenchError, MigrateError
from pilot.integrations.marketplace import Marketplace


@pytest.fixture(autouse=True)
def workload(monkeypatch):
    """A stopped workload runs no job, so the steps skip the background-job wait unless a test starts it."""
    manager = MagicMock()
    manager.is_running.return_value = False
    monkeypatch.setattr("pilot.managers.processes.local.ProcessManager.for_bench", lambda bench: manager)
    return manager


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
    app.is_linked = False
    app.installed_hash = "1111111"
    app.update_target.return_value = RevisionPin(kind="commit", ref="2222222")
    app.has_migrate_changes.return_value = True
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
    app.is_linked = False
    app.installed_hash = "aaaaaaa"
    app.update_target.return_value = RevisionPin(kind="tag", ref="v2.0.0")
    app.has_migrate_changes.return_value = True
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
    site.active_apps.return_value = apps
    return site


def test_create_update_only_includes_sites_with_a_selected_app(tmp_path: Path) -> None:
    """Updating 'hrms' shouldn't back up/migrate a site that doesn't have it installed."""
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    hrms = MagicMock()
    hrms.config.name = "hrms"
    hrms.config.repo = ""
    hrms.is_linked = False
    hrms.installed_hash = "1111111"
    hrms.update_target.return_value = RevisionPin(kind="commit", ref="2222222")
    hrms.has_migrate_changes.return_value = True
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
    hrms.is_linked = False
    hrms.installed_hash = "1111111"
    hrms.update_target.return_value = RevisionPin(kind="commit", ref="2222222")
    hrms.has_migrate_changes.return_value = True
    mock_bench.apps.return_value = [hrms]
    mock_bench.sites.return_value = [_site_with_apps("unknown.local", [])]

    from pilot.core.bench.migration.store import MigrationStore

    with patch.object(Marketplace, "registry", return_value=[]):
        operation = MigrationStore(mock_bench).create_update({"hrms"})

    assert [site.name for site in operation.sites] == ["unknown.local"]


def test_create_update_skips_backup_and_migrate_when_no_app_needs_a_migrate(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    hrms = MagicMock()
    hrms.config.name = "hrms"
    hrms.is_linked = False
    hrms.config.repo = ""
    hrms.installed_hash = "1111111"
    hrms.update_target.return_value = RevisionPin(kind="commit", ref="2222222")
    hrms.has_migrate_changes.return_value = False
    mock_bench.apps.return_value = [hrms]
    mock_bench.sites.return_value = [_site_with_apps("with-hrms.local", ["frappe", "hrms"])]

    from pilot.core.bench.migration.store import MigrationStore

    with patch.object(Marketplace, "registry", return_value=[]):
        operation = MigrationStore(mock_bench).create_update({"hrms"})
    with patch("pilot.tasks.update.UpdateTask.queue", return_value="task-1"):
        operation.begin()

    assert operation.sites == []
    assert operation.state == "updating"
    assert [step["command"] for step in operation.chain] == ["update"]
    mock_bench.site.return_value.set_maintenance_mode.assert_not_called()


def test_create_update_migrates_every_site_when_one_app_needs_it(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    apps = []
    for name, has_migrate_changes in (("hrms", True), ("crm", False)):
        app = MagicMock()
        app.config.name = name
        app.is_linked = False
        app.config.repo = ""
        app.installed_hash = "1111111"
        app.update_target.return_value = RevisionPin(kind="commit", ref="2222222")
        app.has_migrate_changes.return_value = has_migrate_changes
        apps.append(app)
    mock_bench.apps.return_value = apps
    mock_bench.sites.return_value = [
        _site_with_apps("hr.local", ["frappe", "hrms"]),
        _site_with_apps("sales.local", ["frappe", "crm"]),
    ]

    from pilot.core.bench.migration.store import MigrationStore

    with patch.object(Marketplace, "registry", return_value=[]):
        operation = MigrationStore(mock_bench).create_update()

    assert [site.name for site in operation.sites] == ["hr.local", "sales.local"]


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
    site_mock.set_maintenance_settings.assert_called_with({"maintenance_mode": 0, "pause_scheduler": 1})


def test_operation_failure_and_retry_resumes(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    site_dir = tmp_path / "sites" / "site1.localhost"
    site_dir.mkdir(parents=True)
    site_mock = MagicMock()
    site_mock.path = site_dir
    site_mock.maintenance_mode = False
    site_mock.maintenance_settings = {"maintenance_mode": 0, "pause_scheduler": 0}
    site_mock.migrate.side_effect = MigrateError(
        "Patch failure in v15", output="Executing patch_x in site1\nError"
    )
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

    # Test retry resumes
    op.retry()
    assert op.state == "migrating"
    assert op.sites[0].migration_status == "pending"
    assert op.failed_site is None


def test_chain_hands_resources_off_from_current_task(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.state = get_state("backing_up")
    operation.chain.append({"command": "migration-backup", "task_id": "task-101", "site": "site1.localhost"})

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
    assert operation.apps[0].compare_url == ("https://github.com/frappe/frappe/compare/1111111...2222222")
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


def test_update_failure_arms_the_revert_chain_without_the_user(tmp_path: Path) -> None:
    """Nothing in the update phase touches a site database, so a failure there
    reverts on its own instead of parking on needs_attention."""
    from pilot.exceptions import AppValidationError

    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench._update_apps.side_effect = AppValidationError("hooks.py is missing app_name")

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench)._create(
        "update",
        apps=[AppRevision("frappe", "1111111")],
        apps_filter=None,
        sites=[],
    )
    operation.state = get_state("updating")

    with pytest.raises(AppValidationError):
        operation.update_apps()

    assert operation.state == "reverting_apps"
    assert operation.diagnosis["message"] == "hooks.py is missing app_name"

    with patch("pilot.tasks.revert_apps.RevertAppsTask.queue", return_value="task-201") as queue:
        assert operation.enqueue_next() == "task-201"

    assert queue.called


def test_update_failure_after_the_install_step_also_auto_reverts(tmp_path: Path) -> None:
    """A build or install failure is as database-safe as a validation failure."""
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench._rebuild_assets.side_effect = MigrateError("yarn build failed")

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench)._create(
        "update",
        apps=[AppRevision("frappe", "1111111")],
        apps_filter=None,
        sites=[],
    )
    operation.state = get_state("updating")

    with pytest.raises(MigrateError):
        operation.update_apps()

    assert operation.state == "reverting_apps"
    assert operation.apps_updated is False


def test_migrate_failure_still_waits_for_the_user(tmp_path: Path) -> None:
    """The database is touched by then, so recovery stays an explicit decision."""
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    site_mock = MagicMock()
    site_mock.path = tmp_path / "sites" / "site1.localhost"
    site_mock.migrate.side_effect = MigrateError("patch failed", output="")
    mock_bench.site.return_value = site_mock

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.state = get_state("migrating")

    with pytest.raises(MigrateError):
        operation.migrate_site("site1.localhost")

    assert operation.state == "needs_attention"


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

    # Nothing has changed yet, so there is nothing to revert - the user gets to
    # fix the disk and retry instead.
    assert operation.state == "needs_attention"

    for name, scheduler in (("one.local", 1), ("two.local", 0)):
        sites[name].set_maintenance_settings.assert_called_with(
            {"maintenance_mode": 0, "pause_scheduler": scheduler}
        )

    operation.retry()
    assert operation.next_backup_site() == "one.local"
    assert all(site.backup_status == "pending" for site in operation.sites)
    operation.back_up_site("one.local")
    assert sites["one.local"].migration_backup.create.call_count == 2


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

    operation.revert()
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

    operation.revert()
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

    operation.revert()

    assert operation.state == "reverting_sites"


def test_revert_site_stays_in_reverting_sites_until_every_site_is_done(tmp_path: Path) -> None:
    """Recovering the first of several sites must not re-enter its own phase."""
    mock_bench = MagicMock()
    mock_bench.path = tmp_path

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.sites.append(SiteProgress(name="site2.localhost"))
    for site in operation.sites:
        site.backup_status = "backed_up"
        site.migration_status = "failed"
    operation.state = get_state("needs_attention")

    operation.revert()
    operation.revert_site("site1.localhost")

    assert operation.state == "reverting_sites"
    assert operation.revert_checkpoints["site:site1.localhost"] is True
    assert operation.next_revert_site() == "site2.localhost"

    operation.revert_site("site2.localhost")

    assert operation.state == "restarting"


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


def test_bypass_patch_records_audit(tmp_path: Path) -> None:
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench.site.return_value.maintenance_settings = {"maintenance_mode": 0, "pause_scheduler": 0}
    mock_bench.frappe_call = ["bench"]
    mock_bench.sites_path = tmp_path / "sites"

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.state = get_state("needs_attention")
    operation.failed_site = "site1.localhost"
    operation.diagnosis = {"patch": "frappe.patches.expected"}

    mock_bench.audit_action.reset_mock()
    with patch("pilot.utils.run_command", return_value=MagicMock(returncode=0)):
        operation.bypass_patch("frappe.patches.expected")

    mock_bench.audit_action.assert_called_once()
    category, fields = mock_bench.audit_action.call_args.args
    assert category == "bypass_patch"
    assert fields["operation"] == operation.id
    assert fields["patch"] == "frappe.patches.expected"
    assert operation.decisions[-1]["patch"] == "frappe.patches.expected"


def test_bypass_patch_auto_resumes_migration(tmp_path: Path) -> None:
    """A successful skip-patch resumes the failed site and returns to migrating."""
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench.site.return_value.maintenance_settings = {"maintenance_mode": 0, "pause_scheduler": 0}
    mock_bench.frappe_call = ["bench"]
    mock_bench.sites_path = tmp_path / "sites"

    from pilot.core.bench.migration.store import MigrationStore

    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    operation.state = get_state("needs_attention")
    operation.failed_site = "site1.localhost"
    operation.sites[0].migration_status = "failed"
    operation.return_state = "migrating"
    operation.diagnosis = {"patch": "frappe.patches.expected"}

    with patch("pilot.utils.run_command", return_value=MagicMock(returncode=0)):
        operation.bypass_patch("frappe.patches.expected")

    assert operation.state == "migrating"
    assert operation.failed_site is None
    assert operation.sites[0].migration_status == "pending"


def _branch_switch_migration(tmp_path: Path):
    mock_bench = MagicMock()
    mock_bench.path = tmp_path

    from pilot.core.bench.migration.store import MigrationStore

    switched = AppRevision("myapp", "1111111", updated_sha="2222222", branch="main")
    return mock_bench, MigrationStore(mock_bench).create_site_migrate(
        "site1.localhost", switched_app=switched
    )


def test_a_branch_switch_migration_does_not_update_the_app_again(tmp_path: Path) -> None:
    mock_bench, operation = _branch_switch_migration(tmp_path)
    operation.state = get_state("backing_up")
    mock_bench.site.return_value.migration_backup.create.return_value = None

    operation.back_up_site("site1.localhost")

    assert operation.state == "migrating"


def test_reverting_a_branch_switch_migration_returns_the_app_to_its_branch(tmp_path: Path) -> None:
    """Restoring only the databases would leave the sites on the new branch's code."""
    mock_bench, operation = _branch_switch_migration(tmp_path)
    operation.state = get_state("needs_attention")
    operation.sites[0].backup_status = "backed_up"
    operation.sites[0].migration_status = "failed"
    app = mock_bench.app.return_value

    operation.revert()
    assert operation.state == "reverting_apps"
    operation.revert_apps()

    app.return_to.assert_called_once_with("main", "1111111")
    app.record_branch.assert_called_once_with()
    mock_bench._reinstall_apps.assert_called_once()
    assert mock_bench._reinstall_apps.call_args.args[0] == {"myapp"}


def _backing_up_operation(tmp_path: Path):
    from pilot.core.bench.migration.store import MigrationStore

    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench.apps.return_value = []
    mock_bench.site.return_value.maintenance_settings = {"maintenance_mode": 0, "pause_scheduler": 0}
    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    with patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-101"):
        operation.begin()
    operation.site("site1.localhost").backup_status = "backing_up"
    return operation, mock_bench


def test_a_killed_backup_task_pauses_the_operation_for_a_retry(tmp_path: Path) -> None:
    operation, mock_bench = _backing_up_operation(tmp_path)

    operation.fail_stopped_step("task-101", "backing_up", "site1.localhost")

    assert operation.state == "needs_attention"
    assert operation.site("site1.localhost").backup_status == "failed"
    assert "stopped before it finished" in operation.diagnosis["message"]
    mock_bench.site.return_value.set_maintenance_settings.assert_called_with(
        {"maintenance_mode": 0, "pause_scheduler": 0}
    )
    mock_bench.site.return_value.set_maintenance_mode.reset_mock()
    operation.retry()
    mock_bench.site.return_value.set_maintenance_mode.assert_called_once_with(True)
    assert operation.site("site1.localhost").original_config == {"maintenance_mode": 0, "pause_scheduler": 0}
    assert operation.state == "backing_up"
    assert operation.next_backup_site() == "site1.localhost"


def test_a_stopped_task_callback_leaves_the_operation_to_the_next_task(tmp_path: Path) -> None:
    operation, _mock_bench = _backing_up_operation(tmp_path)
    operation.back_up_site("site1.localhost")
    operation.chain.append({"command": "migrate", "task_id": "task-102", "site": "site1.localhost"})
    state = operation.state

    operation.fail_stopped_step("task-101", "backing_up", "site1.localhost")

    assert operation.state == state != "needs_attention"


def test_chain_tasks_register_the_stopped_step_callback() -> None:
    from pilot.internal.tasks.callbacks import validate_callback
    from pilot.tasks.migrate import MigrateTask
    from pilot.tasks.migration_backup import MigrationBackupTask
    from pilot.tasks.update import UpdateTask

    for task, phase in (
        (MigrationBackupTask, "backing_up"),
        (MigrateTask, "migrating"),
        (UpdateTask, "updating"),
    ):
        args = task.__new__(task)
        args.operation_id, args.site = "op-1", "site1.localhost"
        assert task.fail_migration_step(args)["phase"] == phase
    assert (
        validate_callback({"operation": "fail-migration-step", "args": {}})["operation"]
        == "fail-migration-step"
    )


def test_a_task_killed_before_it_queued_the_next_step_pauses_for_a_retry(tmp_path: Path) -> None:
    operation, _mock_bench = _backing_up_operation(tmp_path)
    operation.sites.append(SiteProgress(name="site2.localhost"))
    operation.site("site1.localhost").backup_status = "backed_up"

    operation.fail_stopped_step("task-101", "backing_up", "site1.localhost")

    assert (operation.state, operation.failed_site) == ("needs_attention", None)
    operation.retry()
    assert operation.state == "backing_up"
    assert operation.site("site1.localhost").backup_status == "pending"
    assert operation.next_backup_site() == "site1.localhost"


def test_a_killed_migration_keeps_what_it_touched_for_the_revert(tmp_path: Path) -> None:
    from pilot.core.bench.migration.state import get_state

    operation, mock_bench = _backing_up_operation(tmp_path)
    mock_bench.site.return_value.path = tmp_path
    (tmp_path / "touched_tables.json").write_text('["tabItem"]')
    operation.state = get_state("migrating")
    operation.site("site1.localhost").migration_status = "running"

    operation.fail_stopped_step("task-101", "migrating", "site1.localhost")

    site = operation.site("site1.localhost")
    assert operation.state == "needs_attention"
    assert (site.migration_status, site.touched_tables) == ("failed", ["tabItem"])


@pytest.mark.parametrize("phase", ["reverting_apps", "reverting_sites", "restarting"])
def test_a_stopped_revert_chain_can_resume_from_its_checkpoint(tmp_path, phase):
    operation, _ = _backing_up_operation(tmp_path)
    operation.state = get_state(phase)
    operation.site("site1.localhost").backup_status = "backed_up"
    operation.site("site1.localhost").migration_status = "recovering"
    operation.apps = [AppRevision(name="frappe", sha="old")]
    if phase != "reverting_apps":
        operation.revert_checkpoints["apps"] = True
    if phase == "restarting":
        operation.revert_checkpoints["site:site1.localhost"] = True

    operation.fail_stopped_step("task-101", phase, "site1.localhost")

    assert operation.state == "revert_failed"
    assert operation.can_revert
    assert "stopped" in operation.diagnosis["message"]
    operation.revert()
    assert operation.state == phase


@pytest.mark.parametrize("task_name", ["retry_update", "revert_migration", "bypass_patch"])
def test_an_interrupted_action_recovers_only_before_it_queues_a_chain_task(tmp_path, monkeypatch, task_name):
    import importlib

    from pilot.internal.tasks.callbacks import run_callback

    operation, bench = _backing_up_operation(tmp_path)
    operation.state = get_state("needs_attention")
    operation.return_state = "backing_up"
    operation.failed_site = "site1.localhost"
    module = importlib.import_module(f"pilot.tasks.{task_name}")
    task_class = next(
        value for name, value in vars(module).items() if name.endswith("Task") and name != "Task"
    )
    task = task_class.__new__(task_class)
    task.bench, task.operation_id = bench, operation.id
    bench.migrations.get.return_value = operation
    spec = {"operation": "fail-migration-step", "args": task.fail_migration_step()}
    monkeypatch.setattr("pilot.core.bench.Bench", lambda root: bench)

    operation.retry()
    run_callback(spec, {"bench_root": str(tmp_path), "task_id": "action-task"})
    assert operation.state == "needs_attention"

    operation.retry()
    operation.chain.append({"task_id": "next-task", "command": "migration-backup"})
    run_callback(spec, {"bench_root": str(tmp_path), "task_id": "action-task"})
    assert operation.state == "backing_up"


@pytest.mark.parametrize("with_site", [False, True])
def test_retrying_a_failed_final_restart_does_not_migrate_sites_again(tmp_path, with_site):
    operation, bench = _backing_up_operation(tmp_path)
    operation.state = get_state("migrating")
    if with_site:
        operation.site("site1.localhost").migration_status = "success"
    else:
        operation.sites.clear()
    bench.reload_workers.side_effect = BenchError("restart failed")

    with pytest.raises(BenchError, match="restart failed"):
        operation.complete()
    bench.reload_workers.assert_called_with(raises=True)
    assert operation.state == "needs_attention"
    assert "Restarting services failed: restart failed" in operation.diagnosis["message"]
    operation.fail_stopped_step("task-101", "migrating", None)
    assert "Restarting services failed" in operation.diagnosis["message"]
    operation.retry()

    assert operation.state.next_step(operation) == ("complete-migration", {})
    bench.site.return_value.migrate.assert_not_called()
    bench.reload_workers.side_effect = None
    operation.complete()
    assert operation.state == "completed"


def test_an_interruption_in_the_retrying_state_can_be_retried_or_reverted(tmp_path):
    operation, _ = _backing_up_operation(tmp_path)
    operation.state = get_state("needs_attention")
    operation.return_state = "migrating"
    operation.failed_site = "site1.localhost"
    operation.site("site1.localhost").migration_status = "failed"
    operation.site("site1.localhost").backup_status = "backed_up"
    transition = operation._transition

    def interrupt_after_retrying(target):
        transition(target)
        if target == "retrying":
            raise SystemExit("injected crash")

    with patch.object(operation, "_transition", interrupt_after_retrying), pytest.raises(SystemExit):
        operation.retry()
    assert operation.state == "retrying"
    operation.fail_stopped_step("task-101", "needs_attention", "site1.localhost")
    assert operation.state == "needs_attention"
    assert operation.can_revert
    operation.retry()
    assert operation.state == "migrating"


def _site_with_jobs(pending: list[bool] | Exception) -> MagicMock:
    site = MagicMock()
    site.maintenance_settings = {"maintenance_mode": 0, "pause_scheduler": 0}
    type(site).has_pending_background_jobs = PropertyMock(side_effect=pending)
    return site


def test_a_migration_waits_until_the_site_runs_no_background_job(tmp_path: Path, workload) -> None:
    from pilot.core.bench.migration.store import MigrationStore

    workload.is_running.return_value = True
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    site = _site_with_jobs([True, True, False])
    site.path = tmp_path
    mock_bench.site.return_value = site
    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost", with_safeguards=False)
    operation.state = get_state("migrating")

    with patch("pilot.core.bench.migration.operation.time.sleep") as sleep:
        operation.migrate_site("site1.localhost")

    assert sleep.call_count == 2
    site.migrate.assert_called_once_with()
    assert operation.state == "completed"


def test_jobs_that_do_not_finish_pause_before_the_backup(tmp_path: Path, workload) -> None:
    from pilot.core.bench.migration.store import MigrationStore

    workload.is_running.return_value = True
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    site = _site_with_jobs(lambda: True)
    mock_bench.site.return_value = site
    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    with patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-101"):
        operation.begin()

    clock = iter([0, 100, 301])
    with (
        patch("pilot.core.bench.migration.operation.time.monotonic", side_effect=lambda: next(clock)),
        patch("pilot.core.bench.migration.operation.time.sleep"),
        pytest.raises(BenchError, match="still has background jobs after 300 seconds"),
    ):
        operation.back_up_site("site1.localhost")

    site.migration_backup.create.assert_not_called()
    assert operation.state == "needs_attention"
    assert operation.failed_site == "site1.localhost"
    assert operation.site("site1.localhost").backup_status == "pending"
    site.set_maintenance_settings.assert_called_with({"maintenance_mode": 0, "pause_scheduler": 0})
    operation.retry()
    assert operation.next_backup_site() == "site1.localhost"


def test_jobs_that_do_not_finish_pause_before_the_code_changes(tmp_path: Path, workload) -> None:
    from pilot.core.bench.migration.store import MigrationStore

    workload.is_running.return_value = True
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    mock_bench.site.return_value = _site_with_jobs(BenchError("redis is down"))
    operation = MigrationStore(mock_bench)._create(
        "update", apps=[AppRevision("frappe", "1111111")], apps_filter=None, sites=["site1.localhost"]
    )
    operation.state = get_state("updating")

    with pytest.raises(BenchError, match="redis is down"):
        operation.update_apps()

    mock_bench._update_apps.assert_not_called()
    assert operation.state == "needs_attention"
    assert operation.failed_site is None
    assert "The step did not start" in operation.diagnosis["message"]
    operation.retry()
    assert operation.state == "updating"


def test_web_restarts_once_after_maintenance_mode_is_turned_on(tmp_path: Path, workload) -> None:
    from pilot.core.bench.migration.store import MigrationStore

    workload.is_running.return_value = True
    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    site = _site_with_jobs(lambda: False)
    site.path = tmp_path
    mock_bench.site.return_value = site
    operation = MigrationStore(mock_bench).create_site_migrate("site1.localhost")
    with patch("pilot.tasks.migration_backup.MigrationBackupTask.queue", return_value="task-101"):
        operation.begin()
    mock_bench.reload_workers.assert_not_called()

    operation.back_up_site("site1.localhost")
    assert mock_bench.reload_workers.call_args_list == [call(web_only=True, raises=True)]
    assert operation.is_web_restarted_in_maintenance is True

    mock_bench.reload_workers.reset_mock()
    operation.migrate_site("site1.localhost")
    assert mock_bench.reload_workers.call_args_list == [call(raises=True), call(web_only=True, raises=True)]


def test_web_restarts_after_maintenance_mode_is_turned_off(tmp_path: Path) -> None:
    operation, mock_bench = _backing_up_operation(tmp_path)
    operation.state = get_state("migrating")
    operation.site("site1.localhost").migration_status = "success"
    events = []
    mock_bench.site.return_value.set_maintenance_settings.side_effect = lambda settings: events.append("lift")
    mock_bench.reload_workers.side_effect = lambda **kwargs: events.append(kwargs)

    operation.complete()

    assert events == [{"raises": True}, "lift", {"web_only": True, "raises": True}]


def test_a_failed_web_restart_stops_the_backup(tmp_path: Path, workload) -> None:
    from pilot.core.bench import Bench

    operation, bench = _backing_up_operation(tmp_path)
    bench.reload_workers = Bench.reload_workers.__get__(bench)
    bench.site.return_value = _site_with_jobs(lambda: False)
    workload.is_running.return_value = True
    workload.reload_workers.side_effect = BenchError("web restart failed")

    with pytest.raises(BenchError, match="web restart failed"):
        operation.back_up_site("site1.localhost")

    bench.site.return_value.migration_backup.create.assert_not_called()
    assert not operation.is_web_restarted_in_maintenance


def test_a_failed_web_restart_does_not_complete_the_operation(tmp_path: Path, workload) -> None:
    from pilot.core.bench import Bench

    operation, bench = _backing_up_operation(tmp_path)
    operation.state = get_state("migrating")
    operation.site("site1.localhost").migration_status = "success"
    bench.reload_workers = Bench.reload_workers.__get__(bench)
    workload.reload_workers.side_effect = [None, BenchError("web restart failed")]

    with pytest.raises(BenchError, match="web restart failed"):
        operation.complete()

    assert operation.state == "needs_attention"
    assert "web restart failed" in operation.diagnosis["message"]
    bench.site.return_value.migration_backup.discard.assert_not_called()


def test_recording_an_action_keeps_what_the_action_already_changed(tmp_path: Path) -> None:
    """The API read the operation before it queued Retry; the task may have moved it on since."""
    from pilot.core.bench.migration.store import MigrationStore

    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    store = MigrationStore(mock_bench)
    stale = store.create_site_migrate("site1.localhost")
    moved = store.get(stale.id)
    moved.state = get_state("backing_up")
    store.save(moved)

    store.record_action(stale.id, "retry", "task-301")

    stored = store.get(stale.id)
    assert stored.state == "backing_up"
    assert stored.task_ids == {"retry": "task-301"}


def test_a_chain_task_waits_until_its_queuer_has_recorded_it(tmp_path: Path) -> None:
    """A copy read before the record would drop it when saved, and a failure would then go unnoticed."""
    from pilot.core.bench.migration.store import MigrationStore

    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    store = MigrationStore(mock_bench)
    operation = store.create_site_migrate("site1.localhost")
    reads = []

    def get(operation_id):
        reads.append(operation_id)
        if len(reads) == 2:
            recorded = MigrationStore.get(store, operation_id)
            recorded.chain.append({"command": "migrate", "task_id": "task-401", "site": "site1.localhost"})
            store.save(recorded)
        return MigrationStore.get(store, operation_id)

    with patch.object(store, "get", side_effect=get), patch("pilot.core.bench.migration.store.time.sleep"):
        loaded = store.get_for_chain_task(operation.id, "task-401", "migrate", {"site": "site1.localhost"})

    assert len(reads) == 2
    assert loaded.chain[-1]["task_id"] == "task-401"


def test_a_chain_task_records_itself_when_its_queuer_stopped_first(tmp_path: Path) -> None:
    from pilot.core.bench.migration.store import MigrationStore

    mock_bench = MagicMock()
    mock_bench.path = tmp_path
    store = MigrationStore(mock_bench)
    operation = store.create_site_migrate("site1.localhost")
    clock = iter([0, 10, 31])

    with (
        patch("pilot.core.bench.migration.store.time.monotonic", side_effect=lambda: next(clock)),
        patch("pilot.core.bench.migration.store.time.sleep"),
    ):
        store.get_for_chain_task(operation.id, "task-501", "migrate", {"site": "site1.localhost"})

    assert store.get(operation.id).chain == [
        {"command": "migrate", "task_id": "task-501", "site": "site1.localhost"}
    ]


@pytest.mark.parametrize("suffix", ["", "."])
def test_a_stopped_step_names_the_error_the_task_printed(tmp_path: Path, suffix: str) -> None:
    from pilot.internal.tasks.callbacks import _last_task_error

    task_dir = tmp_path / "tasks" / "task-101"
    task_dir.mkdir(parents=True)
    (task_dir / "output.log").write_text(
        "<14>1 2026-10-27T04:25:21+00:00 host migration-backup 1 - - STEP backup,1.0 Backing up\n"
        "<14>1 2026-10-27T04:25:21+00:00 host migration-backup 1 - - Error: [Errno 28] No space left on device\n"
        "<14>1 2026-10-27T04:25:21+00:00 host migration-backup 1 - - Error: 1\n"
    )
    reason = _last_task_error({"bench_root": str(tmp_path), "task_id": "task-101"})
    assert reason == "[Errno 28] No space left on device"

    operation, _mock_bench = _backing_up_operation(tmp_path)
    operation.fail_stopped_step("task-101", "backing_up", "site1.localhost", reason + suffix)
    assert operation.diagnosis["message"] == (
        "The task stopped before it finished: [Errno 28] No space left on device. Retry it, or revert."
    )


def test_a_killed_task_has_no_error_to_name(tmp_path: Path) -> None:
    from pilot.internal.tasks.callbacks import _last_task_error

    task_dir = tmp_path / "tasks" / "task-101"
    task_dir.mkdir(parents=True)
    (task_dir / "output.log").write_text("<14>1 t host migrate 1 - - Executing patch\n")
    assert _last_task_error({"bench_root": str(tmp_path), "task_id": "task-101"}) == ""
