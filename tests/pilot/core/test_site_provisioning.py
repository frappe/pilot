"""Cert issuance during site provisioning must not disturb sibling vhosts."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from pilot.config import BenchConfig, SiteConfig
from pilot.core.bench import Bench
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
