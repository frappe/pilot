from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot.config import RoutePolicy
from pilot.core.bench import Bench
from pilot.core.site.rename import SiteRename
from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exchange_paths
from tests.pilot.integrations.test_central_client import _bench

OLD = "old.example.com"
NEW = "new.example.com"


@pytest.fixture(autouse=True)
def _no_backup_schedule():
    """Keep the host's real crontab out of these tests."""
    with patch("pilot.managers.cron.CronManager.move_schedule"):
        yield


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


@pytest.mark.parametrize("old,new", [("../outside", NEW), (OLD, "../outside"), (".", NEW), ("", NEW)])
def test_rename_paths_are_checked_before_creating_recovery_files(tmp_path: Path, old: str, new: str) -> None:
    bench = _bench(tmp_path)
    with pytest.raises(BenchError, match="within the bench"):
        SiteRename(bench.site(old), new).run(lambda message: None)
    assert not (bench.config_path / "site-renames").exists()


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


def test_old_requests_keep_resolving_after_nginx_reload_returns(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    _rename(bench)

    assert (bench.sites_path / OLD).is_symlink()
    assert (bench.sites_path / OLD / "site_config.json").read_bytes() == (
        bench.sites_path / NEW / "site_config.json"
    ).read_bytes()


def test_kept_hostnames_keep_their_paths_after_the_hold_expires(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, db_name="original")
    _rename(bench)
    with patch("pilot.core.bench.site_aliases.time.time", return_value=time.time() + 1000):
        bench.site_aliases.release_expired()
    assert _config(bench, OLD)["db_name"] == "original"
    assert (bench.sites_path / OLD).is_symlink()


def test_repeated_renames_keep_older_paths_direct(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, db_name="original")
    _rename(bench)
    third = "third.example.com"
    _rename(bench, old=NEW, new=third)
    assert (bench.sites_path / OLD).readlink() == Path(third)
    assert (bench.sites_path / NEW).readlink() == Path(third)
    assert _config(bench, OLD)["db_name"] == "original"


def test_a_pending_destination_cannot_be_renamed_again(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, db_name="original")
    with (
        patch.object(SiteRename, "_finish", side_effect=RuntimeError("interrupted")),
        pytest.raises(RuntimeError, match="interrupted"),
    ):
        _rename(bench)
    with pytest.raises(BenchError, match="previous rename is pending"):
        _rename(bench, old=NEW, new="third.example.com")
    assert _config(bench, OLD)["db_name"] == "original"
    _rename(bench)


def test_readers_keep_the_old_path_during_failed_renames_and_retries(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, db_name="original")
    stopped = threading.Event()
    started = threading.Event()
    errors = []
    reads = 0

    def read_config():
        nonlocal reads
        started.set()
        while not stopped.is_set():
            try:
                assert _config(bench, OLD)["db_name"] == "original"
                reads += 1
            except (OSError, ValueError, KeyError, AssertionError) as error:
                errors.append(error)

    reader = threading.Thread(target=read_config)
    reader.start()
    try:
        assert started.wait(timeout=5)
        current = OLD
        with patch("pilot.core.site.rename.exchange_paths", wraps=exchange_paths) as exchange:
            for index in range(100):
                target = f"renamed-{index}.example.com"
                with (
                    patch.object(SiteRename, "_finish", side_effect=RuntimeError("interrupted")),
                    pytest.raises(RuntimeError, match="interrupted"),
                ):
                    _rename(bench, old=current, new=target)
                _rename(bench, old=current, new=target)
                current = target
            assert exchange.call_count == 100
    finally:
        stopped.set()
        reader.join(timeout=5)
    assert not reader.is_alive()
    assert reads > 0
    assert not errors


def test_a_failed_reload_preserves_both_paths_and_can_resume(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, db_password="s3cret")

    with (
        patch.object(SiteRename, "_reload_nginx", side_effect=RuntimeError("nginx is unhappy")),
        pytest.raises(RuntimeError, match="nginx is unhappy"),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    old = bench.sites_path / OLD
    assert old.is_symlink() and old.resolve() == bench.sites_path / NEW
    assert _config(bench, OLD)["db_password"] == "s3cret"
    pending = SiteRename(bench.site(OLD), NEW).progress_path
    assert pending.exists()
    assert pending.stat().st_mode & 0o777 == 0o600
    assert pending.parent.stat().st_mode & 0o777 == 0o700
    _rename(bench)
    assert not pending.exists()


def test_primary_hostname_waits_until_nginx_accepts_the_new_site(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, host_name=f"https://{OLD}")
    with (
        patch.object(SiteRename, "_reload_nginx", side_effect=RuntimeError("reload failed")),
        pytest.raises(RuntimeError, match="reload failed"),
    ):
        SiteRename(bench.site(OLD), NEW, make_primary=True).run(lambda message: None)
    assert _config(bench, OLD)["host_name"] == f"https://{OLD}"
    _rename(bench, make_primary=True)
    assert _config(bench, NEW)["host_name"] == f"http://{NEW}"


def test_unfinished_move_reserves_both_names_across_benches(tmp_path: Path) -> None:
    from pilot.utils import host_owner

    bench = _bench(tmp_path)
    other = _bench(tmp_path, "other")
    _site(bench, OLD)
    with (
        patch.object(SiteRename, "_move_site_directory", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        _rename(bench)
    assert host_owner(other.path, OLD) == bench.config.name
    assert host_owner(other.path, NEW) == bench.config.name
    assert bench.resolve_site_name(NEW) == OLD


def test_pending_move_resolves_both_api_names_before_metadata_finishes(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)
    with (
        patch.object(SiteRename, "_finish", side_effect=RuntimeError("interrupted")),
        pytest.raises(RuntimeError, match="interrupted"),
    ):
        _rename(bench)
    assert bench.resolve_site_name(OLD) == NEW
    assert bench.resolve_site_name(NEW) == NEW


@pytest.mark.parametrize("progress", ["[]", '{"target": []}', '{"target": ["../outside", true, false]}'])
def test_corrupt_progress_preserves_compatibility_paths(tmp_path: Path, progress: str) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)
    _rename(bench, keep_old_hostname=False)
    pending = SiteRename(bench.site(OLD), NEW, keep_old_hostname=False).progress_path
    pending.write_text(progress)
    with patch("pilot.core.bench.site_aliases.time.time", return_value=time.time() + 1000):
        bench.site_aliases.release_expired()
    assert (bench.sites_path / OLD / "site_config.json").exists()


@pytest.mark.parametrize("stage", ["before-exchange", "before-reload"])
def test_a_killed_rename_retains_its_alias_and_resumes_without_rotating_again(
    tmp_path: Path, stage: str
) -> None:
    from admin.backend.internal.session import Session

    bench = _bench(tmp_path)
    old_token = Session(bench).issue_pilot_token(OLD)
    _site(bench, OLD, pilot_auth_token=old_token)
    ready = tmp_path / "before-reload"
    script = """
import sys, time
from pathlib import Path
from pilot.core.bench import Bench
import pilot.core.site.rename as rename_module
from pilot.core.site.rename import SiteRename
SiteRename._add_to_hosts = lambda self: None
def pause(self):
    Path(sys.argv[2]).touch()
    time.sleep(60)
if sys.argv[5] == "before-exchange":
    rename_module.exchange_paths = lambda *args: pause(None)
else:
    SiteRename._reload_nginx = pause
SiteRename(Bench(Path(sys.argv[1])).site(sys.argv[3]), sys.argv[4]).run(lambda message: None)
"""
    process = subprocess.Popen(
        [sys.executable, "-c", script, str(bench.path), str(ready), OLD, NEW, stage], start_new_session=True
    )
    try:
        deadline = time.monotonic() + 10
        while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=5)

    token_after_move = _config(bench, OLD)["pilot_auth_token"]
    assert (token_after_move == old_token) == (stage == "before-exchange")
    with patch("pilot.core.bench.site_aliases.time.time", return_value=time.time() + 1000):
        bench.site_aliases.release_expired()
        assert (bench.sites_path / OLD / "site_config.json").exists()
        with pytest.raises(BenchError, match="pending"):
            bench.site_aliases.release(NEW if stage == "before-exchange" else OLD)
    _rename(bench)
    new_token = _config(bench, NEW)["pilot_auth_token"]
    assert new_token != old_token
    if stage == "before-reload":
        assert new_token == token_after_move
    assert Session(bench).verify_token(old_token) is None
    with pytest.raises(BenchError, match="moments"):
        bench.site_aliases.release(OLD)


def test_failed_atomic_exchange_leaves_the_original_site_available(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    with (
        patch.object(SiteRename, "_reload_nginx"),
        patch("pilot.core.site.rename.exchange_paths", side_effect=OSError("exchange failed")),
        pytest.raises(OSError, match="exchange failed"),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert (bench.sites_path / OLD).is_dir()
    assert not (bench.sites_path / NEW).exists()


@pytest.mark.parametrize("write_completed", [False, True])
def test_failed_alias_record_leaves_the_original_site_available(
    tmp_path: Path, write_completed: bool
) -> None:
    from pilot.core.bench.site_aliases import SiteAliases

    bench = _bench(tmp_path)
    _site(bench, OLD, db_password="original-password")
    original = (bench.sites_path / OLD / "site_config.json").read_bytes()
    write = SiteAliases._write

    def fail_write(aliases, renamed):
        if write_completed:
            write(aliases, renamed)
        raise OSError("alias registry write failed")

    with (
        patch.object(SiteAliases, "_write", fail_write),
        patch.object(SiteRename, "_release_route") as release,
        pytest.raises(OSError, match="alias registry write failed"),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    old = bench.sites_path / OLD
    assert old.is_dir() and not old.is_symlink()
    assert (old / "site_config.json").read_bytes() == original
    assert not (bench.sites_path / NEW).exists()
    assert not (bench.sites_path / NEW).is_symlink()
    release.assert_not_called()
    _rename(bench)
    assert _config(bench, NEW)["db_password"] == "original-password"


def test_a_failed_rename_can_simply_be_run_again(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    with (
        patch.object(SiteRename, "_reload_nginx", side_effect=RuntimeError("nginx is unhappy")),
        contextlib.suppress(RuntimeError),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    _rename(bench)

    assert (bench.sites_path / NEW / "site_config.json").exists()
    assert (bench.sites_path / OLD).resolve() == bench.sites_path / NEW


def test_a_failure_part_way_through_keeps_the_forward_state_for_retry(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)
    common = bench.sites_path / "common_site_config.json"
    common.write_text(json.dumps({"default_site": OLD}))

    with (
        patch.object(SiteRename, "_retarget_hostname_aliases", side_effect=RuntimeError("aliases failed")),
        pytest.raises(RuntimeError, match="aliases failed"),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert json.loads(common.read_text())["default_site"] == NEW
    assert (bench.sites_path / OLD).resolve() == bench.sites_path / NEW
    _rename(bench)
    assert (bench.sites_path / NEW).is_dir()


def test_a_failed_rename_keeps_the_new_route_for_recovery(tmp_path: Path) -> None:
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

    assert released == []


def test_the_old_hostname_keeps_being_served(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    _rename(bench)

    assert _config(bench, NEW)["domains"] == [OLD]


def test_the_site_token_is_reissued_for_the_new_name(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, pilot_auth_token="old-token")

    with (
        patch(
            "admin.backend.internal.session.Session.issue_pilot_token",
            return_value="new-token",
        ) as issue_token,
        patch("admin.backend.internal.session.Session.revoke_token", return_value=True) as revoke_token,
    ):
        _rename(bench)

    assert _config(bench, NEW)["pilot_auth_token"] == "new-token"
    issue_token.assert_called_once_with(NEW)
    revoke_token.assert_called_once_with("old-token")


def test_a_failed_rename_reuses_its_new_token_and_revokes_the_old_one_on_completion(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, pilot_auth_token="old-token")

    with (
        patch(
            "admin.backend.internal.session.Session.issue_pilot_token",
            return_value="new-token",
        ) as issue_token,
        patch("admin.backend.internal.session.Session.revoke_token") as revoke_token,
    ):
        with (
            patch.object(SiteRename, "_reload_nginx", side_effect=RuntimeError("reload failed")),
            pytest.raises(RuntimeError, match="reload failed"),
        ):
            SiteRename(bench.site(OLD), NEW).run(lambda message: None)

        assert _config(bench, OLD)["pilot_auth_token"] == "new-token"
        revoke_token.assert_not_called()
        _rename(bench)
        issue_token.assert_called_once_with(NEW)
        revoke_token.assert_called_once_with("old-token")


def test_a_revocation_sync_failure_does_not_roll_back_the_rename(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, pilot_auth_token="old-token")

    with (
        patch(
            "admin.backend.internal.session.Session.issue_pilot_token",
            return_value="new-token",
        ),
        patch(
            "admin.backend.internal.session.Session.revoke_token",
            side_effect=OSError("directory sync failed"),
        ),
        patch.object(SiteRename, "_reload_nginx"),
        pytest.raises(OSError, match="directory sync failed"),
    ):
        SiteRename(bench.site(OLD), NEW).run(lambda message: None)

    assert _config(bench, NEW)["pilot_auth_token"] == "new-token"
    assert (bench.sites_path / OLD).resolve() == bench.sites_path / NEW


def test_a_committed_revocation_sync_failure_still_releases_the_old_route(
    tmp_path: Path,
) -> None:
    from admin.backend.internal.session import RevokedTokens, Session

    bench = _bench(tmp_path)
    token = Session(bench).issue_pilot_token(OLD)
    _site(bench, OLD, pilot_auth_token=token)
    released = []
    add = RevokedTokens.add

    def commit_then_fail(store, key, exp):
        add(store, key, exp)
        raise OSError("directory sync failed")

    with (
        patch.object(RevokedTokens, "add", commit_then_fail),
        patch(
            "pilot.core.adapters.domain_provider.DomainRouteProvider.release",
            lambda self, domain: released.append(domain),
        ),
    ):
        _rename(bench, keep_old_hostname=False)

    assert released == [OLD]


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


def test_make_primary_makes_the_new_name_the_canonical_host(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, ssl=True)

    _rename(bench, make_primary=True)

    assert _config(bench, NEW)["host_name"] == f"https://{NEW}"


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


def test_the_sender_name_follows_the_rename(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, email_sender_name="Old")

    _rename(bench)

    assert _config(bench, NEW)["email_sender_name"] == "New"


def test_a_custom_sender_name_survives_a_rename(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD, email_sender_name="Acme Support")

    _rename(bench)

    assert _config(bench, NEW)["email_sender_name"] == "Acme Support"


def test_a_backup_schedule_follows_the_site_to_its_new_name(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    _site(bench, OLD)

    with patch("pilot.managers.cron.CronManager.move_schedule") as move_schedule:
        _rename(bench)

    old_key, new_key, command = move_schedule.call_args.args
    assert (old_key, new_key) == (OLD, NEW)
    assert f"backup_site {bench.path} {NEW}" in command
