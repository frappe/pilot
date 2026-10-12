"""Cert issuance during site provisioning must not disturb sibling vhosts."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

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
    return SiteProvisioner(bench, "site1.example.com", ["frappe"], "admin", "mariadb")


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
    ssl_ready_flags = [call.kwargs["ssl_ready"] for call in manager.generate_config.call_args_list]
    assert ssl_ready_flags == [True]
    mock_letsencrypt.return_value.obtain.assert_called_once_with(site.config)
    manager.reload.assert_called_once()


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
    ):
        site = provisioner.provision(lambda _: None)

    enable_scheduler.assert_called_once_with()

    assert site.config.ssl is True
    assert site.config.route == route
    persisted = json.loads((site.path / "site_config.json").read_text())
    assert persisted["ssl"] is True
    assert persisted["route"]["origin_scheme"] == "http"
    obtain_cert.assert_not_called()


def test_provision_builds_app_captured_before_install_creates_asset_link(tmp_path: Path) -> None:
    provisioner = _provisioner(tmp_path)
    provisioner.apps.append("gameplan")
    provisioner.bench.create_directories()
    app = MagicMock()
    app.config.name = "gameplan"
    public = tmp_path / "apps" / "gameplan" / "gameplan" / "public"
    public.mkdir(parents=True)
    app.path = public.parent.parent
    link = provisioner.bench.sites_path / "assets" / "gameplan"

    def install_app(site: Site, installed_app: MagicMock) -> None:
        assert installed_app is app
        link.symlink_to(public)

    with (
        patch("pilot.core.site.provisioning.validate_new_site", return_value=False),
        patch("pilot.core.site.provisioning.should_enable_ssl", return_value=False),
        patch.object(provisioner.bench, "apps", return_value=[app]),
        patch.object(provisioner.bench, "is_app_installed", return_value=True),
        patch.object(provisioner.bench, "app", return_value=app),
        patch.object(Site, "create"),
        patch.object(Site, "install_app", install_app),
        patch.object(provisioner, "write_email_sender_name"),
        patch.object(provisioner, "write_route_policy"),
        patch.object(provisioner, "write_pilot_communication_config"),
        patch.object(provisioner.bench, "write_common_site_config"),
        patch.object(Site, "enable_scheduler"),
        patch.object(provisioner, "add_to_hosts"),
        patch.object(provisioner, "reload_nginx"),
        patch("pilot.managers.environment.PythonEnvManager.build_assets_for_app") as build,
    ):
        provisioner.provision(lambda _: None)

    assert link.is_symlink()
    build.assert_called_once_with(app)


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
