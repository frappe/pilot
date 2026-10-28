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
        },
        {
            "domain": "custom.example.com",
            "is_site": False,
            "is_primary": True,
            "public_scheme": "https",
            "tls": True,
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
