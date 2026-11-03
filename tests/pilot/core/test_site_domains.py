import json
from pathlib import Path

from pilot.config import BenchConfig
from pilot.core.bench import Bench


def _domains(tmp_path: Path, config: dict):
    bench = Bench(BenchConfig.default("test-bench"), tmp_path)
    site_path = bench.sites_path / "site.example.com"
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps(config))
    return bench.site("site.example.com").domains


def test_describe_returns_public_route_state(tmp_path: Path) -> None:
    edge_route = {
        "public_scheme": "https",
        "origin_scheme": "http",
        "client_ip_source": "x_forwarded_for",
    }
    domains = _domains(
        tmp_path,
        {
            "route": edge_route,
            "domains": [
                {
                    "domain": "custom.example.com",
                    "route": {
                        "public_scheme": "https",
                        "origin_scheme": "https",
                        "client_ip_source": "proxy_protocol_v2",
                    },
                }
            ],
            "host_name": "https://custom.example.com",
        },
    )

    rows, primary = domains.describe()

    assert primary == "custom.example.com"
    assert rows == [
        {
            "domain": "site.example.com",
            "is_site": True,
            "is_primary": False,
            "public_scheme": "https",
            "tls": True,
            "redirect_to_primary": True,
            "certificate": "not_required",
        },
        {
            "domain": "custom.example.com",
            "is_site": False,
            "is_primary": True,
            "public_scheme": "https",
            "tls": True,
            "redirect_to_primary": False,
            "certificate": "missing",
        },
    ]


def test_describe_domain_returns_none_for_unattached_domain(tmp_path: Path) -> None:
    domains = _domains(tmp_path, {})

    assert domains.describe_domain("other.example.com") is None


def test_describe_domain_uses_attached_hostname_for_case_variant(tmp_path: Path) -> None:
    domains = _domains(
        tmp_path,
        {
            "domains": [
                {
                    "domain": "custom.example.com",
                    "route": {
                        "public_scheme": "https",
                        "origin_scheme": "https",
                        "client_ip_source": "proxy_protocol_v2",
                    },
                }
            ]
        },
    )

    assert domains.describe_domain("CUSTOM.EXAMPLE.COM") == {
        "domain": "custom.example.com",
        "is_site": False,
        "is_primary": False,
        "public_scheme": "https",
        "tls": True,
        "redirect_to_primary": False,
        "certificate": "missing",
    }


def test_a_direct_tls_domain_reports_tls_only_with_a_certificate(tmp_path: Path) -> None:
    from unittest.mock import patch

    from pilot.managers.nginx import NginxManager

    domains = _domains(tmp_path, {"ssl": True, "domains": ["shop.example.com"]})

    with patch.object(NginxManager, "serviceable_tls_domains", return_value=["site.example.com"]):
        rows, _primary = domains.describe()

    assert {row["domain"]: row["tls"] for row in rows} == {
        "site.example.com": True,
        "shop.example.com": False,
    }


def test_a_domain_change_without_a_certificate_email_still_regenerates_nginx(tmp_path: Path) -> None:
    from unittest.mock import patch

    domains = _domains(tmp_path, {"ssl": True})
    domains.site.bench.config.production.enabled = True

    with (
        patch("pilot.tasks.setup_nginx.SetupNginxTask.queue", return_value="nginx-task") as nginx,
        patch("pilot.tasks.setup_letsencrypt.SetupLetsEncryptTask.queue") as letsencrypt,
    ):
        assert domains.is_certificate_email_required
        assert domains.apply_task() == "nginx-task"

    nginx.assert_called_once()
    letsencrypt.assert_not_called()


def test_a_domain_change_with_an_email_issues_certificates(tmp_path: Path) -> None:
    from unittest.mock import patch

    domains = _domains(tmp_path, {"ssl": True})

    with patch(
        "pilot.tasks.setup_letsencrypt.SetupLetsEncryptTask.queue", return_value="tls-task"
    ) as letsencrypt:
        assert domains.apply_task(email="ops@example.com") == "tls-task"

    assert letsencrypt.call_args.kwargs["email"] == "ops@example.com"


def test_a_proxied_domain_change_only_regenerates_nginx(tmp_path: Path) -> None:
    from unittest.mock import patch

    domains = _domains(tmp_path, {"ssl": True})
    domains.site.bench.config.production.enabled = True
    with (
        patch(
            "pilot.core.adapters.domain_provider.DomainRouteProvider.wildcard_domains",
            return_value=["*.example.com"],
        ),
        patch("pilot.tasks.setup_nginx.SetupNginxTask.queue", return_value="nginx-task") as nginx,
        patch("pilot.tasks.setup_letsencrypt.SetupLetsEncryptTask.queue") as letsencrypt,
    ):
        assert not domains.is_certificate_email_required
        assert domains.apply_task(email="ops@example.com") == "nginx-task"
    nginx.assert_called_once()
    letsencrypt.assert_not_called()


def test_default_configs_do_not_share_common_settings() -> None:
    first = BenchConfig.default("first")
    first.letsencrypt.email = "ops@example.com"

    assert BenchConfig.default("second").letsencrypt.email == ""


def test_a_certificate_is_pending_only_while_its_task_is_active(tmp_path: Path) -> None:
    from types import SimpleNamespace
    from unittest.mock import patch

    from pilot.managers.task import TaskStatus

    domains = _domains(tmp_path, {"ssl": True})

    def tasks(*entries: tuple[str, TaskStatus]):
        return [SimpleNamespace(command=command, status=status) for command, status in entries]

    with patch("pilot.managers.task.TaskReader.list_tasks") as list_tasks:
        list_tasks.return_value = tasks(("setup-letsencrypt", TaskStatus.RUNNING))
        assert domains.is_certificate_pending

        list_tasks.return_value = tasks(
            ("setup-letsencrypt", TaskStatus.SUCCESS), ("setup-nginx", TaskStatus.QUEUED)
        )
        assert not domains.is_certificate_pending


def test_a_site_without_tls_needs_no_certificate(tmp_path: Path) -> None:
    domains = _domains(tmp_path, {"domains": ["shop.example.com"]})

    rows, _primary = domains.describe()

    assert {row["domain"]: row["certificate"] for row in rows} == {
        "site.example.com": "not_required",
        "shop.example.com": "not_required",
    }


def test_a_missing_certificate_is_pending_only_while_its_task_runs(tmp_path: Path) -> None:
    from unittest.mock import PropertyMock, patch

    from pilot.core.site.domains import SiteDomains
    from pilot.managers.nginx import NginxManager

    domains = _domains(tmp_path, {"ssl": True, "domains": ["shop.example.com"]})
    config = domains._site_config()

    with (
        patch.object(NginxManager, "serviceable_tls_domains", return_value=["site.example.com"]),
        patch.object(SiteDomains, "is_certificate_pending", new_callable=PropertyMock) as pending,
    ):
        pending.return_value = True
        assert domains.get_certificate_states(config) == {
            "site.example.com": "issued",
            "shop.example.com": "pending",
        }
        pending.return_value = False
        assert domains.get_certificate_states(config)["shop.example.com"] == "missing"


def test_a_custom_domain_can_stop_redirecting_to_the_primary(tmp_path: Path) -> None:
    domains = _domains(
        tmp_path,
        {"domains": ["shop.example.com", "www.example.com"], "host_name": "http://www.example.com"},
    )
    config_path = domains.site.path / "site_config.json"

    domains.set_redirect("SHOP.example.com", False)

    assert json.loads(config_path.read_text())["domains"][0] == {
        "domain": "shop.example.com",
        "redirect_to_primary": False,
    }
    rows, _primary = domains.describe()
    assert {row["domain"]: row["redirect_to_primary"] for row in rows} == {
        "site.example.com": True,
        "shop.example.com": False,
        "www.example.com": False,
    }

    domains.set_redirect("shop.example.com", True)

    assert json.loads(config_path.read_text())["domains"][0] == "shop.example.com"


def test_only_a_custom_domain_has_a_redirect_setting(tmp_path: Path) -> None:
    import pytest

    from pilot.exceptions import DomainConflictError

    domains = _domains(tmp_path, {"domains": ["shop.example.com"]})

    for domain in ("site.example.com", "other.example.com"):
        with pytest.raises(DomainConflictError, match="not a custom domain"):
            domains.set_redirect(domain, False)


def test_attaching_an_attached_domain_only_retries_setup(tmp_path: Path) -> None:
    from unittest.mock import patch

    from pilot.core.site.domains import SiteDomains

    domains = _domains(tmp_path, {"domains": ["shop.example.com"]})

    with patch.object(SiteDomains, "register") as register:
        domains.attach("shop.example.com")
        register.assert_not_called()
        domains.attach("new.example.com")

    register.assert_called_once_with("new.example.com")


def test_dns_check_reports_whether_a_new_domain_points_here(tmp_path: Path) -> None:
    from unittest.mock import patch

    from pilot.core.adapters.domain_provider import DomainRouteProvider

    domains = _domains(tmp_path, {})
    addresses = {"site.example.com": {"198.51.100.7"}, "shop.example.com": {"198.51.100.7"}}

    with (
        patch.object(DomainRouteProvider, "_resolve", side_effect=lambda host: set(addresses.get(host, ()))),
        patch.object(DomainRouteProvider, "server_ip", return_value=""),
    ):
        assert domains.check_dns("shop.example.com") == {"matched": True, "message": ""}
        assert domains.check_dns("late.example.com") == {
            "matched": False,
            "message": "late.example.com doesn't resolve yet. Retry once DNS has updated.",
        }


def test_dns_check_leaves_verification_to_a_domain_provider(tmp_path: Path) -> None:
    from unittest.mock import patch

    from pilot.core.adapters.domain_provider import DomainRouteProvider

    domains = _domains(tmp_path, {})

    with (
        patch(
            "pilot.core.adapters.domain_provider.which", return_value="/usr/local/bin/bench-domain-provider"
        ),
        patch.object(DomainRouteProvider, "verify_dns") as verify_dns,
    ):
        assert domains.check_dns("shop.example.com") == {"matched": True, "message": ""}

    verify_dns.assert_not_called()


def test_dns_check_rejects_an_attached_domain(tmp_path: Path) -> None:
    import pytest

    from pilot.exceptions import DomainConflictError

    domains = _domains(tmp_path, {"domains": ["shop.example.com"]})

    with pytest.raises(DomainConflictError, match="already attached"):
        domains.check_dns("shop.example.com")


def test_a_site_domain_keeps_its_redirect_setting_through_an_entry() -> None:
    from pilot.config import SiteDomain

    entry = {"domain": "shop.example.com", "tls": True, "redirect_to_primary": False}

    assert SiteDomain.from_entry(entry).to_entry() == entry
    assert SiteDomain.from_entry("shop.example.com").to_entry() == "shop.example.com"
