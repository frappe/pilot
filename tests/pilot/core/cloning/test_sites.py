from __future__ import annotations

import json
from contextlib import nullcontext

import pytest

from pilot.core.site.clone import SiteClone
from pilot.core.site.clone_database import SiteDatabaseClone
from pilot.exceptions import BenchError


def test_site_clones_are_fresh_and_files_and_credentials_are_independent(source, monkeypatch):
    imports = []
    original = source.site("source.localhost")
    (original.path / "public/files/alias.txt").symlink_to(original.path / "public/files/marker.txt")
    monkeypatch.setattr(SiteDatabaseClone, "run", lambda self: imports.append(self.config.copy()))
    monkeypatch.setattr(SiteClone, "finish", lambda self, site: None)
    monkeypatch.setattr("pilot.core.bench.cloning.runtime.ForkRuntime", lambda *args, **kwargs: nullcontext())
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


@pytest.mark.parametrize("stage", ["database", "finish"])
@pytest.mark.parametrize("cleanup_fails", [False, True])
def test_failed_site_clone_releases_route_and_owned_resources(source, monkeypatch, stage, cleanup_fails):
    from pilot.core.adapters.domain_provider import DomainRouteProvider

    original = source.site("source.localhost")
    source_config = (original.path / "site_config.json").read_bytes()
    released, dropped = [], []
    monkeypatch.setattr(SiteClone, "validate", lambda self: True)
    monkeypatch.setattr("pilot.core.site.clone.register_with_provider", lambda *args: None)
    monkeypatch.setattr(DomainRouteProvider, "release", lambda self, name: released.append(name))
    monkeypatch.setattr("pilot.core.bench.cloning.runtime.ForkRuntime", lambda *args, **kwargs: nullcontext())

    def fail(*args):
        raise RuntimeError("original clone failure")

    def cleanup(self):
        dropped.append(self.config["db_name"])
        if cleanup_fails:
            raise RuntimeError("database cleanup failure")

    monkeypatch.setattr(SiteDatabaseClone, "cleanup", cleanup, raising=False)
    monkeypatch.setattr(SiteDatabaseClone, "run", fail if stage == "database" else lambda self: None)
    monkeypatch.setattr(SiteClone, "finish", fail)
    with pytest.raises(RuntimeError, match="original clone failure"):
        original.clone("failed.localhost", on_progress=lambda message: None)
    assert released == ["failed.localhost"]
    assert len(dropped) == 1 and dropped[0] != "source_db"
    target = source.sites_path / "failed.localhost"
    assert target.exists() == cleanup_fails
    if cleanup_fails:
        assert json.loads((target / "site_config.json").read_text())["db_name"] == dropped[0]
    assert (original.path / "site_config.json").read_bytes() == source_config
    assert (original.path / "public/files/marker.txt").read_text() == "source files"


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


@pytest.mark.parametrize("existing_hosts_entry", [False, True])
def test_late_clone_failure_removes_owned_hosts_and_refreshes_nginx(
    source, monkeypatch, existing_hosts_entry
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
    monkeypatch.setattr(SiteDatabaseClone, "cleanup", lambda self: events.append("database removed"))
    monkeypatch.setattr(SiteCommands, "set_admin_password", lambda *args: None)
    monkeypatch.setattr(SiteProvisioner, "write_pilot_communication_config", lambda *args: None)
    monkeypatch.setattr(SiteProvisioner, "add_to_hosts", lambda *args: events.append("hosts added"))
    monkeypatch.setattr(
        "pilot.managers.platform.remove_hosts_entry", lambda name: events.append("hosts removed")
    )
    monkeypatch.setattr("pilot.core.bench.cloning.runtime.ForkRuntime", lambda *args, **kwargs: nullcontext())

    def reload(self):
        if (source.sites_path / "failed.localhost").exists():
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
