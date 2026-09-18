"""Cert issuance during site provisioning must not disturb sibling vhosts."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.config import BenchConfig, RoutePolicy, SiteConfig
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
    ):
        site = provisioner.provision(lambda _: None)

    assert site.config.ssl is True
    assert site.config.route == route
    persisted = json.loads((site.path / "site_config.json").read_text())
    assert persisted["ssl"] is True
    assert persisted["route"]["origin_scheme"] == "http"
    obtain_cert.assert_not_called()
