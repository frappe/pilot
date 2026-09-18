import pytest

from pilot.config import SiteConfig

SITE = "site.example.com"


def _site(cert_name: str) -> SiteConfig:
    return SiteConfig(name=SITE, apps=[], cert_name=cert_name)


def test_an_unpinned_site_uses_its_own_name() -> None:
    assert SiteConfig(name=SITE, apps=[]).certificate_name == SITE


def test_a_valid_pin_is_honoured() -> None:
    assert _site("old.example.com").certificate_name == "old.example.com"


@pytest.mark.parametrize(
    "cert_name",
    [
        "../../etc/passwd",  # escapes /etc/letsencrypt/live
        "..",
        "/etc/letsencrypt/live/other",  # absolute path
        "name with spaces",
        "x;\nssl_certificate /etc/shadow;",  # an nginx directive of its own
        "a/b",
        "",
    ],
)
def test_anything_that_is_not_a_hostname_falls_back_to_the_site(cert_name: str) -> None:
    assert _site(cert_name).certificate_name == SITE


def test_a_rejected_pin_never_reaches_the_rendered_config(tmp_path) -> None:
    from pilot.config import BenchConfig
    from pilot.core.bench import Bench
    from pilot.managers.nginx import NginxConfigRenderer

    data = {
        "bench": {"name": "b", "python": "3.14"},
        "apps": [{"name": "frappe", "repo": "x", "branch": "v"}],
        "redis": {"cache_port": 13000, "queue_port": 11000},
    }
    renderer = NginxConfigRenderer(Bench(BenchConfig._from_dict(data), tmp_path))
    renderer._proxy_servers_cache = []
    site = _site("../../etc/passwd")
    site.ssl = True

    config = renderer.generate_bench_config([(site, site.tls_domains)], admin_ssl=False)

    assert "../.." not in config
    assert f"/etc/letsencrypt/live/{SITE}/fullchain.pem" in config


def test_all_domains_never_repeats_a_hostname() -> None:
    """A repeat would render twice inside one nginx server_name."""
    site = SiteConfig(
        name="shop.example.com",
        apps=[],
        domains=["shop.example.com", "Shop.Example.COM", "www.example.com"],
    )

    assert site.all_domains == ["shop.example.com", "www.example.com"]


def test_all_domains_keeps_the_site_first() -> None:
    site = SiteConfig(name="site.example.com", apps=[], domains=["a.example.com", "b.example.com"])

    assert site.all_domains == ["site.example.com", "a.example.com", "b.example.com"]
