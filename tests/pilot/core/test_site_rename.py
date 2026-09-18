from __future__ import annotations

import contextlib
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import RoutePolicy
from pilot.core.bench import Bench
from pilot.core.site.rename import SiteRename
from pilot.exceptions import BenchError
from tests.pilot.integrations.test_central_client import _bench

OLD = "old.example.com"
NEW = "new.example.com"


def _site(bench: Bench, name: str, **config) -> None:
    site_dir = bench.sites_path / name
    site_dir.mkdir(parents=True, exist_ok=True)
    (site_dir / "site_config.json").write_text(json.dumps(config))


def _rename(bench: Bench, old: str = OLD, new: str = NEW, **kwargs) -> SiteRename:
    """Run a rename with nginx stubbed out - the reload needs a real server."""
    rename = SiteRename(bench.site(old), new, **kwargs)
    with patch.object(SiteRename, "_reload_nginx"), patch.object(SiteRename, "run_followups"):
        rename.run(lambda message: None)
    return rename


def _config(bench: Bench, name: str) -> dict:
    return json.loads((bench.sites_path / name / "site_config.json").read_text())


def test_the_site_directory_moves(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    _rename(bench)

    assert (bench.sites_path / NEW / "site_config.json").exists()


def test_the_old_path_still_resolves_until_nginx_has_swung_over(tmp_path: Path) -> None:
    """The old path remains resolvable until nginx reloads."""
    bench = _bench(tmp_path)
    _site(bench, OLD)
    seen = {}

    def record_reload(self) -> None:
        link = bench.sites_path / OLD
        seen["is_symlink"] = link.is_symlink()
        seen["resolves"] = (link / "site_config.json").exists()

    with patch.object(SiteRename, "_reload_nginx", record_reload), patch.object(SiteRename, "run_followups"):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert seen == {"is_symlink": True, "resolves": True}


def test_the_compatibility_link_is_gone_afterwards(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    _rename(bench)

    assert not (bench.sites_path / OLD).is_symlink()
    assert not (bench.sites_path / OLD).exists()


def test_a_failed_reload_rolls_the_rename_back(tmp_path: Path) -> None:
    """A failed reload restores the old directory."""
    bench = _bench(tmp_path)
    _site(bench, OLD, db_password="s3cret")
    original = (bench.sites_path / OLD / "site_config.json").read_text()

    with (
        patch.object(SiteRename, "_reload_nginx", side_effect=RuntimeError("nginx is unhappy")),
        pytest.raises(RuntimeError, match="nginx is unhappy"),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    old = bench.sites_path / OLD
    assert old.is_dir() and not old.is_symlink()
    assert not (bench.sites_path / NEW).exists()
    assert (old / "site_config.json").read_text() == original


def test_failed_compatibility_link_cleanup_rolls_the_rename_back(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    with (
        patch.object(SiteRename, "_reload_nginx"),
        patch.object(SiteRename, "_drop_compatibility_link", side_effect=OSError("unlink failed")),
        pytest.raises(OSError, match="unlink failed"),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert (bench.sites_path / OLD).is_dir()
    assert not (bench.sites_path / NEW).exists()


def test_a_rolled_back_rename_can_simply_be_run_again(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    with (
        patch.object(SiteRename, "_reload_nginx", side_effect=RuntimeError("nginx is unhappy")),
        contextlib.suppress(RuntimeError),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    _rename(bench)

    assert (bench.sites_path / NEW / "site_config.json").exists()
    assert not (bench.sites_path / OLD).exists()


def test_a_failure_part_way_through_restores_what_had_changed(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)
    common = bench.sites_path / "common_site_config.json"
    common.write_text(json.dumps({"default_site": OLD}))

    with (
        patch.object(SiteRename, "_retarget_hostname_aliases", side_effect=RuntimeError("aliases failed")),
        pytest.raises(RuntimeError, match="aliases failed"),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert json.loads(common.read_text())["default_site"] == OLD
    assert (bench.sites_path / OLD).is_dir()
    assert not (bench.sites_path / NEW).exists()


def test_a_rolled_back_rename_gives_the_new_route_back(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)
    released = []

    with (
        patch(
            "pilot.core.adapters.domain_provider.DomainRouteProvider.release",
            lambda self, domain: released.append(domain),
        ),
        patch.object(SiteRename, "_reload_nginx", side_effect=RuntimeError("nginx is unhappy")),
        contextlib.suppress(RuntimeError),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert released == [NEW]


def test_the_old_hostname_keeps_being_served(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    _rename(bench)

    assert _config(bench, NEW)["domains"] == [OLD]


def test_the_old_hostname_can_be_released_instead(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    _rename(bench, keep_old_hostname=False)

    assert _config(bench, NEW).get("domains") in (None, [])


def test_edge_tls_route_is_persisted_after_rename(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)
    route = RoutePolicy("https", "http", "x_forwarded_for")

    with patch(
        "pilot.core.adapters.domain_provider.DomainRouteProvider.register",
        return_value=route,
    ):
        _rename(bench)

    saved = _config(bench, NEW)
    assert saved["ssl"] is True
    assert saved["route"]["origin_scheme"] == "http"


def test_a_canonical_host_naming_the_old_site_moves_with_it(tmp_path: Path) -> None:
    """The old canonical host moves with the site."""
    bench = _bench(tmp_path)
    _site(bench, OLD, host_name=f"http://{OLD}")

    _rename(bench)

    assert _config(bench, NEW)["host_name"] == f"http://{NEW}"


def test_a_canonical_host_follows_the_site_scheme(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=True, host_name=f"https://{OLD}")

    _rename(bench)

    assert _config(bench, NEW)["host_name"] == f"https://{NEW}"


def test_a_canonical_host_naming_another_domain_is_left_alone(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, domains=["shop.customer.com"], host_name="http://shop.customer.com")

    _rename(bench)

    assert _config(bench, NEW)["host_name"] == "http://shop.customer.com"


def test_a_rename_introduces_no_redirect_for_other_domains(tmp_path: Path) -> None:
    """A rename does not make the new name canonical."""
    bench = _bench(tmp_path)
    _site(bench, OLD, domains=["shop.customer.com"])

    _rename(bench)

    config = _config(bench, NEW)
    assert "host_name" not in config
    assert config["domains"] == ["shop.customer.com", OLD]


def test_an_already_listed_old_hostname_is_not_duplicated(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, domains=[OLD])

    _rename(bench)

    assert _config(bench, NEW)["domains"] == [OLD]


def test_the_default_site_follows_the_rename(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)
    common = bench.sites_path / "common_site_config.json"
    common.write_text(json.dumps({"default_site": OLD}))

    _rename(bench)

    assert json.loads(common.read_text())["default_site"] == NEW


def test_a_rename_does_not_redeploy_production(tmp_path: Path) -> None:
    """Rename follow-up does not redeploy production."""
    bench = _bench(tmp_path)
    _site(bench, OLD)
    bench.config.production.enabled = True

    with (
        patch.object(Bench, "setup_production") as setup_production,
        patch.object(SiteRename, "_reload_nginx"),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    setup_production.assert_not_called()


def test_a_tls_site_still_refreshes_its_certificate(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=True)
    bench.config.production.enabled = True

    with patch.object(Bench, "setup_letsencrypt") as letsencrypt, patch.object(SiteRename, "_reload_nginx"):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    letsencrypt.assert_called_once()


def test_the_inventory_ignores_a_symlinked_site_directory(tmp_path: Path) -> None:
    """The compatibility symlink is not counted as a second site."""
    bench = _bench(tmp_path)
    _site(bench, NEW)
    (bench.sites_path / OLD).symlink_to(NEW)

    assert [site.config.name for site in bench.sites()] == [NEW]


def test_the_certificate_is_requested_after_nginx_serves_the_new_name(tmp_path: Path) -> None:
    """Certificate issuance follows nginx publication of the new name."""
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=True)
    order = []

    with (
        patch.object(SiteRename, "_reload_nginx", lambda self: order.append("nginx")),
        patch.object(Bench, "setup_letsencrypt", lambda self: order.append("certbot")),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert order == ["nginx", "certbot"]


def test_a_custom_tls_domain_alone_still_triggers_reissue(tmp_path: Path) -> None:
    """A custom TLS domain is reissued after a cloud-site rename."""
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=False, domains=[{"domain": "shop.customer.com", "tls": True}])
    called = []

    with (
        patch.object(SiteRename, "_reload_nginx"),
        patch.object(Bench, "setup_letsencrypt", lambda self: called.append("certbot")),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert called == ["certbot"]


def test_a_fully_plain_site_needs_no_certificate(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=False, domains=["www.example.com"])
    called = []

    with (
        patch.object(SiteRename, "_reload_nginx"),
        patch.object(Bench, "setup_letsencrypt", lambda self: called.append("certbot")),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert called == []


# --- the certificate has to survive the rename ------------------------------


def test_the_certificate_lineage_is_pinned_so_https_never_drops(tmp_path: Path) -> None:
    """Pinning the lineage keeps HTTPS active during a rename."""
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=True)

    with patch("pilot.managers.nginx.cert_files_exist", return_value=True):
        _rename(bench)

    assert _config(bench, NEW)["cert_name"] == OLD


def test_the_renamed_site_still_finds_its_certificate(tmp_path: Path) -> None:
    from pilot.managers.nginx import NginxManager

    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=True)

    with patch("pilot.managers.nginx.cert_files_exist", return_value=True):
        _rename(bench)

    renamed = next(site for site in bench.sites() if site.config.name == NEW)
    assert NginxManager(bench).cert_path(renamed.config).parent.name == OLD


def test_nothing_is_pinned_when_there_is_no_certificate(tmp_path: Path) -> None:
    """A site without a certificate uses its new name as the lineage."""
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=True)

    with patch("pilot.managers.nginx.cert_files_exist", return_value=False):
        _rename(bench)

    assert "cert_name" not in _config(bench, NEW)


def test_an_already_pinned_lineage_is_left_alone(tmp_path: Path) -> None:
    """A second rename keeps the original certificate lineage."""
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=True, cert_name="original.example.com")

    with patch("pilot.managers.nginx.cert_files_exist", return_value=True):
        _rename(bench)

    assert _config(bench, NEW)["cert_name"] == "original.example.com"


# --- the site config is never written over blind ----------------------------


def test_a_malformed_site_config_stops_the_rename(tmp_path: Path) -> None:
    """A malformed site config prevents destructive rename writes."""
    bench = _bench(tmp_path)
    site_dir = bench.sites_path / OLD
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{not json")

    with pytest.raises(BenchError, match="not valid JSON"):
        _rename(bench)


def test_a_malformed_config_is_caught_before_anything_moves(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    site_dir = bench.sites_path / OLD
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text("{not json")

    with contextlib.suppress(BenchError):
        _rename(bench)

    assert (bench.sites_path / OLD / "site_config.json").read_text() == "{not json"
    assert not (bench.sites_path / NEW).exists()


def test_credentials_survive_a_rename(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, db_name="_abc", db_password="s3cret", encryption_key="k3y")

    _rename(bench)

    saved = _config(bench, NEW)
    assert saved["db_password"] == "s3cret"
    assert saved["encryption_key"] == "k3y"
    assert saved["db_name"] == "_abc"


def test_a_released_hostname_takes_its_lineage_with_it(tmp_path: Path) -> None:
    """Releasing a hostname also drops an unused certificate lineage."""
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=True)

    with patch("pilot.managers.nginx.cert_files_exist", return_value=True):
        _rename(bench, keep_old_hostname=False)

    assert "cert_name" not in _config(bench, NEW)


# --- collisions inside one bench --------------------------------------------


def test_a_rename_onto_another_sites_custom_domain_is_refused(tmp_path: Path) -> None:
    """A rename cannot claim another site's custom domain."""
    bench = _bench(tmp_path)
    _site(bench, OLD)
    _site(bench, "other.example.com", domains=["shop.customer.com"])

    with pytest.raises(BenchError, match="already a domain of this bench's site"):
        SiteRename(bench.site(OLD), "shop.customer.com").validate()


def test_a_rename_may_reuse_the_sites_own_domain(tmp_path: Path) -> None:
    """Its own alias is not a collision - the site already answers to it."""
    bench = _bench(tmp_path)
    _site(bench, OLD, domains=["www.example.com"])

    SiteRename(bench.site(OLD), "www.example.com").validate()


def test_promoting_an_alias_to_the_site_name_leaves_no_duplicate(tmp_path: Path) -> None:
    """The promoted alias is removed from the alias list."""
    bench = _bench(tmp_path)
    _site(bench, OLD, domains=["www.example.com"])

    _rename(bench, new="www.example.com")

    site = next(s for s in bench.sites() if s.config.name == "www.example.com")
    assert site.config.all_domains == ["www.example.com", OLD]
    assert _config(bench, "www.example.com")["domains"] == [OLD]


# --- releasing a hostname must not strand the site's other TLS domains -------


def test_releasing_a_hostname_keeps_the_lineage_when_tls_domains_remain(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=False, domains=[{"domain": "shop.customer.com", "tls": True}])

    with patch("pilot.managers.nginx.cert_files_exist", return_value=True):
        _rename(bench, keep_old_hostname=False)

    assert _config(bench, NEW)["cert_name"] == OLD


def test_releasing_a_hostname_drops_the_lineage_when_nothing_else_uses_tls(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=False, domains=["plain.example.com"])

    with patch("pilot.managers.nginx.cert_files_exist", return_value=True):
        _rename(bench, keep_old_hostname=False)

    assert "cert_name" not in _config(bench, NEW)
