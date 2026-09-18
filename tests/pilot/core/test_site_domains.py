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
