"""Cert issuance during site provisioning must not disturb sibling vhosts."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.config import BenchConfig, RoutePolicy, SiteConfig
from pilot.config.site import email_sender_name
from pilot.core.bench import Bench
from pilot.core.site import Site
from pilot.core.site.provisioning import SiteProvisioner

_BASE_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
    "production": {"enabled": True},
}


def _provisioner(tmp_path: Path) -> SiteProvisioner:
    bench = Bench(BenchConfig._from_dict(_BASE_DATA), tmp_path)
    bench.config.letsencrypt.email = "ops@example.com"
    return SiteProvisioner(bench, "site1.example.com", ["frappe"], "admin", "mariadb")


def test_provisioning_records_its_owner_before_site_creation_starts(tmp_path, monkeypatch):
    from pilot.core.site.creation import SiteCreationOwner

    provisioner = _provisioner(tmp_path)
    monkeypatch.setenv("BENCH_TASK_ID", "creating-task")

    def fail_during_creation(*args):
        assert SiteCreationOwner(tmp_path, provisioner.name).is_owned_by("creating-task")
        raise RuntimeError("injected creation failure")

    with (
        patch("pilot.core.site.provisioning.validate_new_site", return_value=False),
        patch.object(provisioner, "_provision", side_effect=fail_during_creation),
        pytest.raises(RuntimeError, match="injected creation failure"),
    ):
        provisioner.provision(lambda message: None)

    assert SiteCreationOwner(tmp_path, provisioner.name).is_owned_by("creating-task")


def test_obtain_cert_never_regenerates_without_ssl(tmp_path: Path) -> None:
    """An ssl_ready=False pass would strip 443 bench-wide while certbot runs."""
    site = MagicMock()
    site.config = SiteConfig(name="site1.example.com", apps=["frappe"])

    with (
        patch("pilot.managers.nginx.NginxManager") as mock_nginx,
        patch("pilot.managers.letsencrypt.LetsEncryptManager") as mock_letsencrypt,
    ):
        _provisioner(tmp_path).obtain_cert(site, lambda _: None)

    manager = mock_nginx.return_value
    manager.generate_config.assert_not_called()
    mock_letsencrypt.return_value.obtain.assert_called_once_with(site.config)
    manager.apply_config.assert_called_once_with()


def test_a_failed_certificate_keeps_the_new_site_on_http(tmp_path: Path) -> None:
    from pilot.exceptions import CommandError

    site = MagicMock()
    site.config = SiteConfig(name="site1.example.com", apps=["frappe"])
    messages: list[str] = []

    with (
        patch("pilot.managers.nginx.NginxManager") as mock_nginx,
        patch("pilot.managers.letsencrypt.LetsEncryptManager") as mock_letsencrypt,
    ):
        mock_letsencrypt.return_value.obtain.side_effect = CommandError("DNS problem")
        _provisioner(tmp_path).obtain_cert(site, messages.append)

    assert [call.args for call in site.set_ssl.call_args_list] == [(True,), (False,)]
    mock_nginx.return_value.generate_config.assert_not_called()
    assert "stays on HTTP" in messages[-1]


def test_no_certificate_email_keeps_the_new_site_on_http(tmp_path: Path) -> None:
    provisioner = _provisioner(tmp_path)
    provisioner.bench.config.letsencrypt.email = ""
    site = MagicMock()

    with patch("pilot.managers.letsencrypt.LetsEncryptManager") as mock_letsencrypt:
        provisioner.obtain_cert(site, lambda _: None)

    site.set_ssl.assert_not_called()
    mock_letsencrypt.return_value.obtain.assert_not_called()


def test_edge_tls_site_sets_ssl_without_obtaining_a_certificate(tmp_path: Path) -> None:
    provisioner = _provisioner(tmp_path)
    route = RoutePolicy("https", "http", "x_forwarded_for")

    def create_site(site: Site, db_type: str | None = None) -> None:
        site.path.mkdir(parents=True)
        (site.path / "site_config.json").write_text(json.dumps({"db_name": "site1"}))

    with (
        patch("pilot.core.site.provisioning.validate_new_site", return_value=True),
        patch("pilot.core.site.provisioning.register_with_provider", return_value=route),
        patch.object(Site, "create", create_site),
        patch.object(provisioner, "install_apps"),
        patch.object(provisioner, "write_pilot_communication_config"),
        patch.object(provisioner, "build_missing_assets"),
        patch.object(provisioner, "add_to_hosts"),
        patch.object(provisioner, "reload_nginx"),
        patch.object(provisioner, "obtain_cert") as obtain_cert,
        patch.object(provisioner.bench, "write_common_site_config"),
        patch.object(Site, "enable_scheduler") as enable_scheduler,
        patch("pilot.core.bench.backups.BenchBackups.install_schedule"),
    ):
        site = provisioner.provision(lambda _: None)

    enable_scheduler.assert_called_once_with()

    assert site.config.ssl is True
    assert site.config.route == route
    persisted = json.loads((site.path / "site_config.json").read_text())
    assert persisted["ssl"] is True
    assert persisted["route"]["origin_scheme"] == "http"
    obtain_cert.assert_not_called()


def test_only_a_successful_new_site_gets_default_backups(tmp_path: Path) -> None:
    from pilot.config.site_backup import read_retention
    from pilot.exceptions import BenchError

    provisioner = _provisioner(tmp_path)

    def create_site(site: Site, db_type: str | None = None) -> None:
        site.path.mkdir(parents=True)
        (site.path / "site_config.json").write_text('{"db_name": "site1"}')

    with (
        patch("pilot.core.site.provisioning.validate_new_site", return_value=False),
        patch.object(Site, "create", create_site),
        patch.object(Site, "enable_scheduler"),
        patch.object(provisioner, "install_apps") as install_apps,
        patch.object(provisioner, "write_pilot_communication_config"),
        patch.object(provisioner, "build_missing_assets"),
        patch.object(provisioner, "add_to_hosts"),
        patch.object(provisioner, "reload_nginx"),
        patch.object(provisioner.bench, "write_common_site_config"),
        patch("pilot.core.bench.backups.BenchBackups.install_schedule") as install_schedule,
    ):
        site = provisioner.provision(lambda _: None)
        assert read_retention(site.path / "site_config.json").calendar_rotation is True
        install_schedule.assert_called_once()

        install_schedule.reset_mock()
        provisioner.name = "failed.example.com"
        install_apps.side_effect = BenchError("app installation failed")
        with pytest.raises(BenchError, match="app installation failed"):
            provisioner.provision(lambda _: None)
        install_schedule.assert_not_called()


@pytest.mark.parametrize("restore_fails", [False, True])
def test_restore_only_enables_backups_after_the_database_and_apps_are_ready(restore_fails: bool) -> None:
    from pilot.core.site.provisioning import provision_from_backup
    from pilot.exceptions import BenchError

    bench = MagicMock()
    site = MagicMock()
    operations = MagicMock()
    site.restore = operations.restore
    site.backups.enable_default_schedule = operations.schedule
    if restore_fails:
        operations.restore.side_effect = BenchError("restore failed")
    with (
        patch.object(Site, "provision", return_value=site) as provision,
        patch("pilot.core.site.apps.SiteApps.restore_installed_apps_copy", operations.apps),
    ):
        if restore_fails:
            with pytest.raises(BenchError, match="restore failed"):
                provision_from_backup(bench, "new.example.com", "database.sql.gz", "admin")
            operations.schedule.assert_not_called()
        else:
            provision_from_backup(bench, "new.example.com", "database.sql.gz", "admin")
            assert [call[0] for call in operations.mock_calls] == ["restore", "apps", "schedule"]
        assert provision.call_args.kwargs["schedule_backups"] is False


def test_a_new_site_calls_pilot_over_loopback_and_gives_the_browser_the_admin_domain(tmp_path: Path) -> None:
    provisioner = _provisioner(tmp_path)
    provisioner.bench.config.admin.domain = "admin.local"
    site = provisioner.bench.site("site1.example.com")
    site.path.mkdir(parents=True)
    (site.path / "site_config.json").write_text("{}")

    with patch("admin.backend.internal.session.Session.issue_pilot_token", return_value="site-token"):
        provisioner.write_pilot_communication_config(site)

    assert json.loads((site.path / "site_config.json").read_text()) == {
        "pilot_endpoint": f"http://127.0.0.1:{provisioner.bench.config.admin.internal_port}",
        "cloud_server_url": "http://admin.local",
        "cloud_settings_embed_url": "http://admin.local",
        "pilot_auth_token": "site-token",
    }


def test_enable_scheduler_runs_frappe_for_the_site(tmp_path: Path) -> None:
    bench = Bench(BenchConfig._from_dict(_BASE_DATA), tmp_path)
    site = Site(SiteConfig(name="site1.example.com", apps=["frappe"]), bench)

    with patch("pilot.core.site.commands.run_command", return_value=MagicMock(returncode=0)) as run:
        site.enable_scheduler()

    command = run.call_args.args[0]
    assert command[-3:] == ["--site", "site1.example.com", "enable-scheduler"]
    assert run.call_args.kwargs["cwd"] == bench.sites_path


def test_enable_scheduler_fails_loudly(tmp_path: Path) -> None:
    from pilot.exceptions import BenchError

    bench = Bench(BenchConfig._from_dict(_BASE_DATA), tmp_path)
    site = Site(SiteConfig(name="site1.example.com", apps=["frappe"]), bench)

    with patch("pilot.core.site.commands.run_command", return_value=MagicMock(returncode=1)):
        try:
            site.enable_scheduler()
        except BenchError as error:
            assert "site1.example.com" in str(error)
        else:
            raise AssertionError("expected BenchError")


@pytest.mark.parametrize(
    ("returncode", "stdout", "expected"),
    [
        (0, "READY for migration: site site1.example.com does not have any background jobs", False),
        (1, "NOT READY for migration: site site1.example.com has pending background jobs", True),
    ],
)
def test_pending_background_jobs_come_from_ready_for_migration(tmp_path, returncode, stdout, expected) -> None:
    from pilot.exceptions import CommandError

    bench = Bench(BenchConfig._from_dict(_BASE_DATA), tmp_path)
    site = Site(SiteConfig(name="site1.example.com", apps=["frappe"]), bench)

    error = CommandError(stdout, returncode=returncode) if returncode else None
    with patch("pilot.core.site.commands.run_command", side_effect=error) as run:
        assert site.has_pending_background_jobs is expected

    assert run.call_args.args[0][-3:] == ["--site", "site1.example.com", "ready-for-migration"]


def test_a_failed_background_job_check_fails_loudly(tmp_path: Path) -> None:
    from pilot.exceptions import BenchError, CommandError

    bench = Bench(BenchConfig._from_dict(_BASE_DATA), tmp_path)
    site = Site(SiteConfig(name="site1.example.com", apps=["frappe"]), bench)
    stderr = 'Traceback (most recent call last):\n  File "x.py", line 1\nredis.exceptions.ConnectionError: refused\n'

    with (
        patch("pilot.core.site.commands.run_command", side_effect=CommandError(stderr)),
        pytest.raises(BenchError, match=r"example\.com: redis\.exceptions\.ConnectionError: refused$"),
    ):
        _ = site.has_pending_background_jobs


def test_a_new_site_sends_mail_under_its_first_label_in_title_case(tmp_path: Path) -> None:
    provisioner = _provisioner(tmp_path)
    site = Site(SiteConfig(name="site1.example.com", apps=["frappe"]), provisioner.bench)
    site.path.mkdir(parents=True)
    (site.path / "site_config.json").write_text(json.dumps({"db_name": "site1"}))

    provisioner.write_email_sender_name(site)

    persisted = json.loads((site.path / "site_config.json").read_text())
    assert persisted == {"db_name": "site1", "email_sender_name": "Site1"}


def test_the_sender_name_turns_dashes_and_underscores_into_spaces() -> None:
    assert email_sender_name("north_star--labs.example.com") == "North Star Labs"
