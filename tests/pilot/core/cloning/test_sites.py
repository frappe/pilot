from __future__ import annotations

import json
from contextlib import nullcontext

import pytest

from pilot.core.site.clone import SiteClone
from pilot.core.site.clone_database import SiteDatabaseClone
from pilot.exceptions import BenchError


@pytest.mark.parametrize("bench_engine", ["mariadb", "postgres"])
@pytest.mark.parametrize("site_engine", ["mariadb", "postgres"])
@pytest.mark.parametrize("destination_engine", ["mariadb", "postgres"])
def test_clone_uses_site_engine_instead_of_bench_defaults(
    source, monkeypatch, bench_engine, site_engine, destination_engine
):
    from pilot.config import BenchConfig
    from pilot.core.bench import Bench

    source.config.db_type = bench_engine
    original = source.site("source.localhost")
    path = original.path / "site_config.json"
    config = json.loads(path.read_text())
    config["db_type"] = site_engine
    path.write_text(json.dumps(config))
    destination = Bench(BenchConfig.default("target"), source.path.parent / "target")
    destination.config.db_type = destination_engine
    destination.create_directories()
    (destination.sites_path / "apps.txt").write_text("frappe\n")
    imported = []
    monkeypatch.setattr(SiteDatabaseClone, "run", lambda self: imported.append(self.config.copy()))
    monkeypatch.setattr(SiteClone, "finish", lambda *args: None)
    monkeypatch.setattr("pilot.core.site.clone_runtime.CloneRuntime", lambda *args, **kwargs: nullcontext())
    cloned = original.clone("copy.localhost", destination, on_progress=lambda message: None)
    assert cloned.db_type == site_engine
    assert imported[0]["db_type"] == site_engine
    assert imported[0]["db_port"] == getattr(destination.config, site_engine).port


def test_clone_rejects_unsupported_site_engine_even_on_mariadb_bench(source):
    original = source.site("source.localhost")
    path = original.path / "site_config.json"
    config = json.loads(path.read_text())
    config["db_type"] = "sqlite"
    path.write_text(json.dumps(config))
    with pytest.raises(BenchError, match="MariaDB and PostgreSQL"):
        original.clone("copy.localhost", on_progress=lambda message: None)


def test_clone_cleans_database_if_config_disappears_during_import(source, monkeypatch):
    cleaned = []

    def fail(database):
        (database.destination.path / "site_config.json").unlink()
        raise RuntimeError("import failed after config disappeared")

    monkeypatch.setattr(SiteDatabaseClone, "run", fail)
    monkeypatch.setattr(
        SiteDatabaseClone, "cleanup", lambda database: cleaned.append(database.config["db_name"])
    )
    with pytest.raises(RuntimeError, match="config disappeared"):
        source.site("source.localhost").clone("failed.localhost", on_progress=lambda message: None)
    assert len(cleaned) == 1
    assert not (source.sites_path / "failed.localhost").exists()


def test_clone_does_not_clean_database_before_import_starts(source, monkeypatch):
    cleaned = []

    def fail(*args):
        raise OSError("config write failed")

    monkeypatch.setattr("pilot.core.site.clone.write_private_text", fail)
    monkeypatch.setattr(
        SiteDatabaseClone, "cleanup", lambda database: cleaned.append(database.config["db_name"])
    )
    with pytest.raises(OSError, match="config write failed"):
        source.site("source.localhost").clone("failed.localhost", on_progress=lambda message: None)
    assert cleaned == []


@pytest.mark.parametrize("password", [None, "chosen-password"])
def test_site_clone_generates_password_unless_explicitly_supplied(source, monkeypatch, password):
    monkeypatch.setattr(SiteClone, "run", lambda self, on_progress: self.password)
    original = source.site("source.localhost")
    first = original.clone("first.localhost", admin_password=password)
    second = original.clone("second.localhost", admin_password=password)
    if password is None:
        assert len(first) >= 24
        assert first != second
    else:
        assert first == second == password


def test_site_clones_are_fresh_and_files_and_credentials_are_independent(source, monkeypatch):
    imports = []
    original = source.site("source.localhost")
    (original.path / "public/files/alias.txt").symlink_to(original.path / "public/files/marker.txt")
    monkeypatch.setattr(SiteDatabaseClone, "run", lambda self: imports.append(self.config.copy()))
    monkeypatch.setattr(SiteClone, "finish", lambda self, site: None)
    monkeypatch.setattr("pilot.core.site.clone_runtime.CloneRuntime", lambda *args, **kwargs: nullcontext())
    first = source.site("source.localhost").clone("first.localhost", on_progress=lambda message: None)
    original = source.site("source.localhost")
    (original.path / "public/files/marker.txt").write_text("new source data")
    second = original.clone("second.localhost", on_progress=lambda message: None)
    assert len(imports) == 2
    assert (first.path / "logs").is_dir()
    assert (first.path / "locks").is_dir()
    assert imports[0]["db_name"] != imports[1]["db_name"] != "source_db"
    assert imports[0]["db_password"] != imports[1]["db_password"] != "source-secret"
    assert imports[0]["encryption_key"] == "source-key"
    assert imports[0]["pause_scheduler"] == imports[0]["mute_emails"] == 1
    assert not {"domains", "redis_cache", "pilot_auth_token"} & imports[0].keys()
    assert (first.path / "public/files/marker.txt").read_text() == "source files"
    assert (first.path / "public/files/alias.txt").resolve() == first.path / "public/files/marker.txt"
    assert (second.path / "public/files/marker.txt").read_text() == "new source data"
    (first.path / "private/files/marker.txt").write_text("changed destination")
    assert (original.path / "private/files/marker.txt").read_text() == "source files"


@pytest.mark.parametrize("stage", ["database", "finish", "domain_cleanup"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_site_clone_releases_route_and_owned_resources(source, monkeypatch, stage, cleanup_fails):
    from pilot.config import RoutePolicy
    from pilot.core.adapters.domain_provider import DomainRouteProvider

    original = source.site("source.localhost")
    source_config = (original.path / "site_config.json").read_bytes()
    released, dropped = [], []
    monkeypatch.setattr(SiteClone, "validate", lambda self: True)
    monkeypatch.setattr(
        "pilot.core.site.clone.register_with_provider", lambda *args: RoutePolicy.direct(False)
    )
    monkeypatch.setattr(DomainRouteProvider, "release", lambda self, name: released.append(name))
    monkeypatch.setattr("pilot.core.site.clone_runtime.CloneRuntime", lambda *args, **kwargs: nullcontext())

    def fail(*args):
        raise RuntimeError("original clone failure")

    if stage == "domain_cleanup":

        def release(self, name):
            released.append(name)
            raise RuntimeError("provider unavailable")

        monkeypatch.setattr(DomainRouteProvider, "release", release)

    def cleanup(self):
        dropped.append(self.config["db_name"])
        if cleanup_fails:
            raise RuntimeError("database cleanup failure")

    monkeypatch.setattr(SiteDatabaseClone, "cleanup", cleanup, raising=False)
    monkeypatch.setattr(SiteDatabaseClone, "run", fail if stage == "database" else lambda self: None)
    monkeypatch.setattr(SiteClone, "finish", fail)
    with pytest.raises(RuntimeError, match="original clone failure") as caught:
        original.clone("failed.localhost", on_progress=lambda message: None)
    if stage == "domain_cleanup":
        assert any("provider unavailable" in note for note in caught.value.__notes__)
    assert released == ["failed.localhost"]
    assert len(dropped) == 1 and dropped[0] != "source_db"
    target = source.sites_path / "failed.localhost"
    assert target.exists() == cleanup_fails
    if cleanup_fails:
        assert json.loads((target / "site_config.json").read_text())["db_name"] == dropped[0]
    assert (original.path / "site_config.json").read_bytes() == source_config
    assert (original.path / "public/files/marker.txt").read_text() == "source files"


@pytest.mark.parametrize("target", ["/another/site/private/secret", "../../private/files/marker.txt", "loop"])
def test_clone_rejects_upload_links_outside_their_upload_tree(source, monkeypatch, target):
    root = source.site("source.localhost").path / "public/files"
    (root / "loop").symlink_to(target)
    monkeypatch.setattr(SiteDatabaseClone, "run", lambda self: None)
    monkeypatch.setattr(SiteDatabaseClone, "cleanup", lambda self: None)
    with pytest.raises(BenchError, match="Upload symlink"):
        source.site("source.localhost").clone("unsafe.localhost", on_progress=lambda message: None)
    assert not source.site("unsafe.localhost").path.exists()


def test_no_provider_policy_does_not_release_a_route(source, monkeypatch):
    from pilot.core.adapters.domain_provider import DomainRouteProvider

    released = []
    monkeypatch.setattr(SiteClone, "validate", lambda self: True)
    monkeypatch.setattr("pilot.core.site.clone.register_with_provider", lambda *args: None)
    monkeypatch.setattr(DomainRouteProvider, "release", lambda self, name: released.append(name))
    monkeypatch.setattr(SiteDatabaseClone, "cleanup", lambda self: None)

    def fail(self):
        raise RuntimeError("clone failed")

    monkeypatch.setattr(SiteDatabaseClone, "run", fail)
    with pytest.raises(RuntimeError, match="clone failed"):
        source.site("source.localhost").clone("failed.localhost", on_progress=lambda message: None)
    assert released == []


def test_failed_route_registration_does_not_release_an_unowned_route(source, monkeypatch):
    from pilot.core.adapters.domain_provider import DomainRouteProvider

    released = []
    monkeypatch.setattr(SiteClone, "validate", lambda self: True)
    monkeypatch.setattr(DomainRouteProvider, "release", lambda self, name: released.append(name))

    def conflict(*args):
        raise BenchError("route already owned")

    monkeypatch.setattr("pilot.core.site.clone.register_with_provider", conflict)
    with pytest.raises(BenchError, match="route already owned"):
        source.site("source.localhost").clone("conflict.localhost", on_progress=lambda message: None)
    assert released == []
    assert not (source.sites_path / "conflict.localhost").exists()


def test_clone_file_cleanup_failure_keeps_original_error(source, monkeypatch):
    def fail(*args):
        raise RuntimeError("original clone failure")

    def cannot_remove(*args):
        raise OSError("cannot remove recovery files")

    monkeypatch.setattr(SiteDatabaseClone, "run", fail)
    monkeypatch.setattr(SiteDatabaseClone, "cleanup", lambda self: None)
    monkeypatch.setattr("pilot.core.site.clone.shutil.rmtree", cannot_remove)
    with pytest.raises(RuntimeError, match="original clone failure") as caught:
        source.site("source.localhost").clone("failed.localhost", on_progress=lambda message: None)
    assert any("cannot remove recovery files" in note for note in caught.value.__notes__)
    assert (source.sites_path / "failed.localhost/site_config.json").exists()


@pytest.mark.parametrize("cleanup_failure", [None, "hosts", "database"])
@pytest.mark.parametrize("existing_hosts_entry", [False, True])
def test_late_clone_failure_removes_owned_hosts_and_refreshes_nginx(
    source, monkeypatch, existing_hosts_entry, cleanup_failure
):
    from pathlib import Path

    from pilot.core.site.commands import SiteCommands
    from pilot.core.site.provisioning import SiteProvisioner
    from pilot.managers.nginx import NginxManager

    events = []
    source.config.production.process_manager = "none"
    read_text = Path.read_text

    def hosts(self, *args, **kwargs):
        if str(self) == "/etc/hosts":
            return "127.0.0.1 failed.localhost\n" if existing_hosts_entry else "127.0.0.1 localhost\n"
        return read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", hosts)
    monkeypatch.setattr(SiteDatabaseClone, "run", lambda self: None)

    def cleanup(self):
        events.append("database removed")
        if cleanup_failure == "database":
            raise RuntimeError("database cleanup failed")

    def remove_hosts(name):
        events.append("hosts removed")
        if cleanup_failure == "hosts":
            raise RuntimeError("hosts cleanup failed")

    monkeypatch.setattr(SiteDatabaseClone, "cleanup", cleanup)
    monkeypatch.setattr(SiteCommands, "set_admin_password", lambda *args: None)
    monkeypatch.setattr(SiteProvisioner, "write_pilot_communication_config", lambda *args: None)
    monkeypatch.setattr(SiteProvisioner, "add_to_hosts", lambda *args: events.append("hosts added"))
    monkeypatch.setattr("pilot.managers.platform.remove_hosts_entry", remove_hosts)
    monkeypatch.setattr("pilot.core.site.clone_runtime.CloneRuntime", lambda *args, **kwargs: nullcontext())

    def reload(self):
        if "database removed" not in events:
            events.append("nginx published")
            raise RuntimeError("nginx publish failed")
        events.append("nginx cleared")

    monkeypatch.setattr(NginxManager, "reload_for_site_change", reload)
    with pytest.raises(RuntimeError, match="nginx publish failed"):
        source.site("source.localhost").clone("failed.localhost", on_progress=lambda message: None)
    assert events == (
        ["hosts added", "nginx published"]
        + ([] if existing_hosts_entry else ["hosts removed"])
        + ["database removed", "nginx cleared"]
    )
    assert (source.sites_path / "failed.localhost").exists() == (cleanup_failure == "database")
