from __future__ import annotations

import json
from pathlib import Path

import pytest

from pilot.commands.sites.rename import RenameSiteCommand
from pilot.config import AppConfig, BenchConfig, MariaDBConfig, RedisConfig, WorkerConfig, WorkerGroup
from pilot.core.bench import Bench
from pilot.core.site.rename import SiteRename
from pilot.exceptions import BenchError


def _bench(root: Path, name: str, admin_domain: str = "") -> Bench:
    bench_dir = root / "benches" / name
    bench_dir.mkdir(parents=True, exist_ok=True)
    config = BenchConfig(
        name=name,
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[WorkerGroup(queues=["default"], count=1)]),
    )
    config.admin.domain = admin_domain
    bench = Bench(config, bench_dir)
    bench.create_directories()
    (bench_dir / "bench.toml").write_text(f'[bench]\nname = "{name}"\n')
    return bench


def _make_site(bench: Bench, name: str, ssl: bool = False) -> None:
    site_dir = bench.sites_path / name
    site_dir.mkdir(parents=True)
    (site_dir / "site_config.json").write_text(json.dumps({"db_name": "x", "ssl": ssl}))


def test_rename_raises_if_old_missing(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "b1")
    with pytest.raises(BenchError, match="does not exist"):
        SiteRename(bench.site("nope.localhost"), "new.localhost").validate()


def test_rename_raises_if_new_already_exists(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "b1")
    _make_site(bench, "old.localhost")
    _make_site(bench, "taken.localhost")
    with pytest.raises(BenchError, match="already exists"):
        SiteRename(bench.site("old.localhost"), "taken.localhost").validate()


def test_rename_raises_if_new_equals_admin_domain(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "b1", admin_domain="admin.localhost")
    _make_site(bench, "old.localhost")
    with pytest.raises(BenchError, match="admin domain"):
        SiteRename(bench.site("old.localhost"), "admin.localhost").validate()


def test_rename_raises_if_new_claimed_by_sibling_bench(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "b1")
    _make_site(bench, "old.localhost")
    # A sibling bench already serves the target hostname.
    sibling = _bench(tmp_path, "b2")
    _make_site(sibling, "wanted.localhost")
    with pytest.raises(BenchError, match="already used by bench 'b2'"):
        SiteRename(bench.site("old.localhost"), "wanted.localhost").validate()


def test_rename_moves_dir_and_updates_default_site(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "b1")
    _make_site(bench, "old.localhost")
    (bench.sites_path / "common_site_config.json").write_text(json.dumps({"default_site": "old.localhost"}))

    RenameSiteCommand(bench, old_name="old.localhost", new_name="new.localhost").run()

    old_path = bench.sites_path / "old.localhost"
    assert old_path.is_symlink() and old_path.readlink() == Path("new.localhost")
    assert (bench.sites_path / "new.localhost" / "site_config.json").exists()
    csc = json.loads((bench.sites_path / "common_site_config.json").read_text())
    assert csc["default_site"] == "new.localhost"


def test_rename_followup_never_redeploys_production(tmp_path: Path, monkeypatch) -> None:
    """A rename refreshes certificates without restarting production."""
    bench = _bench(tmp_path, "b1")
    bench.config.production.enabled = True
    _make_site(bench, "new.localhost", ssl=True)
    calls = []
    monkeypatch.setattr(Bench, "setup_production", lambda self, **kwargs: calls.append("prod"))
    monkeypatch.setattr(Bench, "setup_letsencrypt", lambda self: calls.append("le"))

    SiteRename(bench.site("old.localhost"), "new.localhost").run_followups(on_progress=print)

    assert calls == ["le"]


def test_rename_followup_runs_letsencrypt_for_a_tls_site(tmp_path: Path, monkeypatch) -> None:
    bench = _bench(tmp_path, "b1")
    _make_site(bench, "new.localhost", ssl=True)
    calls = []
    monkeypatch.setattr(Bench, "setup_letsencrypt", lambda self: calls.append("le"))

    SiteRename(bench.site("old.localhost"), "new.localhost").run_followups(on_progress=print)

    assert calls == ["le"]


def test_rename_followup_skips_letsencrypt_for_a_plain_site(tmp_path: Path, monkeypatch) -> None:
    bench = _bench(tmp_path, "b1")
    _make_site(bench, "new.localhost", ssl=False)
    calls = []
    monkeypatch.setattr(Bench, "setup_letsencrypt", lambda self: calls.append("le"))

    SiteRename(bench.site("old.localhost"), "new.localhost").run_followups(on_progress=print)

    assert calls == []


def test_rename_followup_advises_on_failure(
    tmp_path: Path, monkeypatch, capsys: pytest.CaptureFixture
) -> None:
    bench = _bench(tmp_path, "b1")
    _make_site(bench, "new.localhost", ssl=True)

    def boom(self):
        raise BenchError("certbot exploded")

    monkeypatch.setattr(Bench, "setup_letsencrypt", boom)
    SiteRename(bench.site("old.localhost"), "new.localhost").run_followups(on_progress=print)

    out = capsys.readouterr().out
    assert "did not complete" in out
    assert "pilot setup letsencrypt -b b1" in out



def _age_alias(bench: Bench, name: str) -> None:
    import os
    import time

    then = time.time() - 600
    os.utime(bench.sites_path / name, (then, then), follow_symlinks=False)
    registry = bench.site_aliases.registry_path
    if registry.exists():
        recorded = json.loads(registry.read_text())
        registry.write_text(json.dumps({alias: then for alias in recorded}))


def test_rename_back_reclaims_the_old_name_once_requests_are_done(tmp_path: Path) -> None:
    bench = _bench(tmp_path, "b1")
    _make_site(bench, "old.localhost")
    RenameSiteCommand(bench, old_name="old.localhost", new_name="new.localhost").run()

    with pytest.raises(BenchError, match="renamed moments ago"):
        RenameSiteCommand(bench, old_name="new.localhost", new_name="old.localhost").run()
    assert (bench.sites_path / "new.localhost").is_dir()

    _age_alias(bench, "old.localhost")
    RenameSiteCommand(bench, old_name="new.localhost", new_name="old.localhost").run()

    old_path = bench.sites_path / "old.localhost"
    assert old_path.is_dir() and not old_path.is_symlink()
    assert (bench.sites_path / "new.localhost").readlink() == Path("old.localhost")


def test_a_new_site_may_take_a_renamed_sites_old_name(tmp_path: Path) -> None:
    from pilot.core.site.provisioning import validate_new_site

    bench = _bench(tmp_path, "b1")
    _make_site(bench, "new.localhost")
    alias = bench.sites_path / "old.localhost"
    alias.symlink_to("new.localhost")
    _age_alias(bench, "old.localhost")

    validate_new_site(bench, "old.localhost", [])
    bench.site_aliases.release("old.localhost")

    assert not alias.is_symlink()
    assert (bench.sites_path / "new.localhost").is_dir()


def test_rename_stops_before_moving_without_atomic_exchange(tmp_path: Path, monkeypatch) -> None:
    import errno

    def unsupported(first: Path, second: Path) -> None:
        raise OSError(errno.ENOTSUP, "unsupported")

    monkeypatch.setattr("pilot.core.site.rename.exchange_paths", unsupported)
    bench = _bench(tmp_path, "b1")
    _make_site(bench, "old.localhost")

    with pytest.raises(BenchError, match="cannot swap the site directory"):
        RenameSiteCommand(bench, old_name="old.localhost", new_name="new.localhost").run()

    assert (bench.sites_path / "old.localhost" / "site_config.json").exists()
    assert not (bench.sites_path / "new.localhost").exists()
    assert not (bench.sites_path / "new.localhost").is_symlink()


def test_bench_list_does_not_count_a_rename_alias(tmp_path: Path) -> None:
    from pilot.commands.bench.list import ListCommand

    bench = _bench(tmp_path, "b1")
    _make_site(bench, "new.localhost")
    (bench.sites_path / "old.localhost").symlink_to("new.localhost")

    assert ListCommand()._site_count(bench.path) == 1


def test_workload_start_retires_expired_rename_aliases_only(tmp_path: Path) -> None:
    import time

    bench = _bench(tmp_path, "b1")
    _make_site(bench, "new.localhost")
    RenameSiteCommand(bench, old_name="new.localhost", new_name="newer.localhost").run()
    (bench.sites_path / "dev.example.com").symlink_to("newer.localhost")

    bench.site_aliases.release_expired()
    assert (bench.sites_path / "new.localhost").is_symlink()

    registry = json.loads(bench.site_aliases.registry_path.read_text())
    registry["new.localhost"] = time.time() - 600
    bench.site_aliases.registry_path.write_text(json.dumps(registry))
    bench.site_aliases.release_expired()

    assert not (bench.sites_path / "new.localhost").is_symlink()
    assert (bench.sites_path / "dev.example.com").is_symlink()
    assert json.loads(bench.site_aliases.registry_path.read_text()) == {}
