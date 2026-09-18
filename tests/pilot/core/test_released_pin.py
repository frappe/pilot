from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import PropertyMock, patch

import pytest

from pilot.config import SiteConfig
from pilot.core.adapters.domain_provider import DomainRouteProvider
from pilot.core.bench import Bench
from pilot.core.bench.admin_domain import AdminDomainChange, ProductionAdminDomain
from pilot.core.site.provisioning import validate_new_site
from pilot.core.site.rename import SiteRename
from pilot.exceptions import BenchError
from pilot.managers.letsencrypt import LetsEncryptManager
from pilot.managers.nginx import NginxManager
from pilot.utils import host_owner
from tests.pilot.integrations.test_central_client import _bench

OLD = "old.example.com"
NEW = "new.example.com"


def _site(bench: Bench, name: str, **config) -> None:
    site_dir = bench.sites_path / name
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


def _config(bench: Bench, name: str) -> dict:
    return json.loads((bench.sites_path / name / "site_config.json").read_text())


def _released(bench: Bench) -> None:
    """What a rename that gave up OLD leaves behind: still pinned to its lineage."""
    _site(bench, NEW, ssl=False, domains=[{"domain": "shop.customer.com", "tls": True}], cert_name=OLD)


# --- a pinned lineage claims its hostname -----------------------------------


def test_a_pinned_lineage_claims_its_hostname(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _released(bench)

    assert bench.site_claiming(OLD) == NEW


def test_a_new_site_cannot_take_a_pinned_hostname(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _released(bench)

    with pytest.raises(BenchError, match="already claimed by this bench's site"):
        validate_new_site(bench, OLD, [])


def test_the_admin_cannot_take_a_pinned_hostname(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _released(bench)
    bench.config.admin.domain = OLD

    with pytest.raises(BenchError, match="conflicts with this bench's own site"):
        ProductionAdminDomain(bench, "").check()


def test_a_sibling_benchs_pin_claims_the_hostname_too(tmp_path: Path) -> None:
    """Lineages are host-wide, shared by every bench."""
    here = _bench(tmp_path, "b1")
    sibling = _bench(tmp_path, "b2")
    _released(sibling)

    assert host_owner(here.path, OLD) == "b2"


# --- the release completes by moving the certificate -------------------------


def test_a_pin_the_site_no_longer_answers_to_is_released() -> None:
    released = SiteConfig(name=NEW, apps=[], domains=["shop.customer.com"], cert_name=OLD)
    retained = SiteConfig(name=NEW, apps=[], domains=[OLD], cert_name=OLD)

    assert released.has_released_certificate_pin is True
    assert retained.has_released_certificate_pin is False
    assert SiteConfig(name=NEW, apps=[]).has_released_certificate_pin is False


def test_a_released_pin_is_retired_by_issuing_under_the_sites_own_name(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _released(bench)
    issued_as = []

    with patch.object(LetsEncryptManager, "obtain", lambda self, site: issued_as.append(site.cert_name)):
        LetsEncryptManager(bench).obtain_all()

    assert issued_as == [""]  # the site's own name, not the released lineage
    assert "cert_name" not in _config(bench, NEW)
    assert bench.site_claiming(OLD) is None


def test_a_failed_reissue_keeps_the_pin(tmp_path: Path) -> None:
    """The pinned certificate is what still serves the site's TLS domains."""
    from pilot.exceptions import CommandError

    bench = _bench(tmp_path)
    _released(bench)

    def fail(self, site) -> None:
        raise CommandError("certbot failed")

    with patch.object(LetsEncryptManager, "obtain", fail):
        LetsEncryptManager(bench).obtain_all()

    assert _config(bench, NEW)["cert_name"] == OLD


def test_a_retained_pin_is_expanded_in_place(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, NEW, ssl=True, domains=[OLD], cert_name=OLD)
    issued_as = []

    with patch.object(LetsEncryptManager, "obtain", lambda self, site: issued_as.append(site.cert_name)):
        LetsEncryptManager(bench).obtain_all()

    assert issued_as == [OLD]
    assert _config(bench, NEW)["cert_name"] == OLD


# --- provider routes follow the rename ---------------------------------------


def _rename_recording_routes(bench: Bench, **kwargs) -> list[tuple[str, str]]:
    calls: list[tuple[str, str]] = []
    with (
        patch.object(
            DomainRouteProvider, "register", lambda self, site, domain: calls.append(("register", domain))
        ),
        patch.object(DomainRouteProvider, "release", lambda self, domain: calls.append(("release", domain))),
        patch.object(SiteRename, "_reload_nginx"),
        patch.object(SiteRename, "run_followups"),
    ):
        SiteRename(bench.site(OLD), NEW, **kwargs).run(lambda message: None)
    return calls


def test_a_rename_routes_the_new_hostname(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    assert ("register", NEW) in _rename_recording_routes(bench)


def test_a_kept_hostname_keeps_its_route(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    assert ("release", OLD) not in _rename_recording_routes(bench)


def test_a_released_hostname_gives_its_route_back(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    assert _rename_recording_routes(bench, keep_old_hostname=False) == [("register", NEW), ("release", OLD)]


def test_a_failed_move_gives_the_new_route_back(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)
    calls: list[tuple[str, str]] = []

    with (
        patch.object(
            DomainRouteProvider, "register", lambda self, site, domain: calls.append(("register", domain))
        ),
        patch.object(DomainRouteProvider, "release", lambda self, domain: calls.append(("release", domain))),
        patch.object(SiteRename, "_move_site_directory", side_effect=OSError("disk full")),
        pytest.raises(OSError),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert calls == [("register", NEW), ("release", NEW)]


# --- smaller pieces -----------------------------------------------------------


def test_site_scoped_callers_cannot_set_a_pin(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, NEW)

    with pytest.raises(BenchError):
        bench.site(NEW).update_public_config({"cert_name": "future.example.com"})


def test_the_admin_reports_http_when_no_certificate_was_issued(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    bench.config.admin.tls = True

    with patch.object(NginxManager, "has_admin_cert", new_callable=PropertyMock, return_value=False):
        assert AdminDomainChange(bench, "vm-new.zone.example")._served_scheme() == "http"

    with patch.object(NginxManager, "has_admin_cert", new_callable=PropertyMock, return_value=True):
        assert AdminDomainChange(bench, "vm-new.zone.example")._served_scheme() == "https"
