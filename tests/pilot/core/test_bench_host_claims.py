from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilot.config import SiteConfig
from pilot.core.bench import Bench
from pilot.core.bench.admin_domain import ProductionAdminDomain
from pilot.exceptions import BenchError
from tests.pilot.integrations.test_central_client import _bench


def _site(bench: Bench, name: str, **config) -> None:
    site_dir = bench.sites_path / name
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


# --- site_claiming ----------------------------------------------------------


def test_a_sites_own_name_is_claimed(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, "one.example.com")

    assert bench.site_claiming("one.example.com") == "one.example.com"


def test_a_sites_custom_domain_is_claimed(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, "one.example.com", domains=["shop.customer.com"])

    assert bench.site_claiming("shop.customer.com") == "one.example.com"


def test_an_unclaimed_host_is_free(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, "one.example.com")

    assert bench.site_claiming("free.example.com") is None


def test_a_site_can_be_ignored(tmp_path: Path) -> None:
    """A rename checks its target against everything except itself."""
    bench = _bench(tmp_path)
    _site(bench, "one.example.com", domains=["www.example.com"])

    assert bench.site_claiming("www.example.com", ignoring="one.example.com") is None


# --- admin domain -----------------------------------------------------------


def test_the_admin_domain_may_not_take_a_sites_custom_domain(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, "one.example.com", domains=["shop.customer.com"])
    bench.config.admin.domain = "shop.customer.com"

    with pytest.raises(BenchError, match="conflicts with this bench's own site"):
        ProductionAdminDomain(bench, "").check()


def test_the_admin_domain_may_not_take_a_site_name(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, "one.example.com")
    bench.config.admin.domain = "one.example.com"

    with pytest.raises(BenchError, match="conflicts with this bench's own site"):
        ProductionAdminDomain(bench, "").check()


def test_a_free_admin_domain_is_accepted(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, "one.example.com")
    bench.config.admin.domain = "admin.example.com"

    ProductionAdminDomain(bench, "").check()


# --- certificate lineage ----------------------------------------------------


def test_a_pin_naming_another_site_is_not_honoured(tmp_path: Path) -> None:
    """A pinned certificate lineage claims its hostname."""
    bench = _bench(tmp_path)
    _site(bench, "victim.example.com")
    _site(bench, "attacker.example.com", cert_name="victim.example.com")

    attacker = next(s for s in bench.sites() if s.config.name == "attacker.example.com")
    assert bench.certificate_name(attacker.config) == "attacker.example.com"


def test_a_pin_naming_another_sites_custom_domain_is_not_honoured(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, "victim.example.com", domains=["shop.customer.com"])

    site = SiteConfig(name="attacker.example.com", apps=[], cert_name="shop.customer.com")
    assert bench.certificate_name(site) == "attacker.example.com"


def test_a_pin_naming_the_admin_domain_is_not_honoured(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    bench.config.admin.domain = "admin.example.com"

    site = SiteConfig(name="one.example.com", apps=[], cert_name="admin.example.com")
    assert bench.certificate_name(site) == "one.example.com"


def test_a_pin_naming_a_former_hostname_is_honoured(tmp_path: Path) -> None:
    """What a rename leaves behind: nobody else answers to it."""
    bench = _bench(tmp_path)

    site = SiteConfig(name="new.example.com", apps=[], cert_name="former.example.com")
    assert bench.certificate_name(site) == "former.example.com"


def test_a_site_pinned_to_its_own_retained_hostname_is_honoured(tmp_path: Path) -> None:
    """A site's own kept hostname is not a conflict."""
    bench = _bench(tmp_path)
    _site(bench, "new.example.com", domains=["old.example.com"], cert_name="old.example.com")

    site = next(s for s in bench.sites() if s.config.name == "new.example.com")
    assert bench.certificate_name(site.config) == "old.example.com"


def test_a_pin_naming_a_sibling_benchs_site_is_not_honoured(tmp_path: Path) -> None:
    """Sibling benches cannot claim the same hostname."""
    bench = _bench(tmp_path)
    sibling = _bench(tmp_path, name="b2")
    _site(sibling, "old.example.com")

    site = SiteConfig(name="new.example.com", apps=[], cert_name="old.example.com")
    assert bench.certificate_name(site) == "new.example.com"
