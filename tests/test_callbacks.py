"""Tests for admin.backend.tasks.callbacks — failure cleanup must only remove
what the failed task created, never a pre-existing site."""
from __future__ import annotations

from pathlib import Path

import pytest

from admin.backend.tasks import callbacks


@pytest.fixture(autouse=True)
def _no_hosts_writes(monkeypatch):
    # Never let a test touch /etc/hosts via sudo.
    monkeypatch.setattr(callbacks.subprocess, "run", lambda *a, **k: None)


def _make_site(bench_root: Path, name: str) -> Path:
    site = bench_root / "sites" / name
    site.mkdir(parents=True)
    (site / "site_config.json").write_text("{}")
    return site


def test_new_site_failure_callback_removes_created_site(tmp_path: Path) -> None:
    site = _make_site(tmp_path, "broken.localhost")
    callbacks.new_site_failure_callback({"args": {"name": "broken.localhost"}, "bench_root": str(tmp_path)})
    assert not site.exists()


def test_new_site_failure_callback_drops_database_and_dir(tmp_path: Path, monkeypatch) -> None:
    # DB cleanup must be attempted for the failed site, and the dir removed.
    site = _make_site(tmp_path, "broken.localhost")
    dropped: list[str] = []
    monkeypatch.setattr(callbacks, "_drop_site_best_effort", lambda root, name: dropped.append(name))

    callbacks.new_site_failure_callback({"args": {"name": "broken.localhost"}, "bench_root": str(tmp_path)})

    assert dropped == ["broken.localhost"]
    assert not site.exists()


def test_drop_site_best_effort_swallows_errors(tmp_path: Path) -> None:
    # No bench.toml here → _load_bench raises → must be swallowed, never propagate.
    callbacks._drop_site_best_effort(tmp_path, "broken.localhost")


def test_new_site_failure_callback_noop_when_nothing_created(tmp_path: Path) -> None:
    # A validate-stage failure leaves no site dir; the view guard means the name
    # never belonged to a pre-existing site. Callback must not raise.
    (tmp_path / "sites").mkdir()
    callbacks.new_site_failure_callback({"args": {"name": "never-made.localhost"}, "bench_root": str(tmp_path)})


def test_new_site_failure_callback_leaves_sibling_site(tmp_path: Path) -> None:
    sibling = _make_site(tmp_path, "healthy.localhost")
    callbacks.new_site_failure_callback({"args": {"name": "broken.localhost"}, "bench_root": str(tmp_path)})
    assert sibling.exists()


# ── app_fetch_failure_callback — only ever removes what the task cloned ───────


def _make_apps(bench_root: Path, *names: str) -> None:
    apps = bench_root / "apps"
    apps.mkdir(exist_ok=True)
    for name in names:
        (apps / name).mkdir()


def test_created_apps_excludes_pre_existing(tmp_path: Path) -> None:
    _make_apps(tmp_path, "frappe", "erpnext", "newapp")
    assert sorted(callbacks._created_apps(tmp_path, {"frappe", "erpnext"})) == ["newapp"]


def test_app_fetch_failure_callback_only_tears_down_created(tmp_path: Path, monkeypatch) -> None:
    _make_apps(tmp_path, "frappe", "newapp")  # frappe pre-existing, newapp this task
    torn: list[str] = []
    monkeypatch.setattr(callbacks, "_load_bench", lambda root: object())
    monkeypatch.setattr(callbacks, "_teardown_app", lambda bench, root, name: torn.append(name))

    callbacks.app_fetch_failure_callback(
        {"bench_root": str(tmp_path), "pre_existing_apps": ["frappe"], "args": {"name": "newapp"}}
    )
    assert torn == ["newapp"]


def test_app_fetch_failure_callback_noop_without_snapshot(tmp_path: Path, monkeypatch) -> None:
    # No pre_existing_apps key → cannot prove safety → must not load bench or delete.
    _make_apps(tmp_path, "frappe", "newapp")
    monkeypatch.setattr(callbacks, "_load_bench", lambda root: pytest.fail("must not run"))
    callbacks.app_fetch_failure_callback({"bench_root": str(tmp_path), "args": {"name": "newapp"}})


def test_app_fetch_failure_callback_noop_when_all_pre_existing(tmp_path: Path, monkeypatch) -> None:
    _make_apps(tmp_path, "frappe")
    monkeypatch.setattr(callbacks, "_load_bench", lambda root: pytest.fail("must not run"))
    callbacks.app_fetch_failure_callback(
        {"bench_root": str(tmp_path), "pre_existing_apps": ["frappe"], "args": {}}
    )


def test_app_fetch_failure_callback_tears_down_even_if_bench_unloadable(tmp_path: Path, monkeypatch) -> None:
    # Broken bench config must not abandon cleanup — created apps still get removed.
    _make_apps(tmp_path, "frappe", "newapp")
    torn: list[tuple] = []
    monkeypatch.setattr(callbacks, "_load_bench", lambda root: (_ for _ in ()).throw(RuntimeError("bad toml")))
    monkeypatch.setattr(callbacks, "_teardown_app", lambda bench, root, name: torn.append((bench, name)))

    callbacks.app_fetch_failure_callback(
        {"bench_root": str(tmp_path), "pre_existing_apps": ["frappe"], "args": {"name": "newapp"}}
    )
    assert torn == [(None, "newapp")]


def test_force_teardown_removes_dir_and_apps_txt_line(tmp_path: Path) -> None:
    _make_apps(tmp_path, "newapp")
    sites = tmp_path / "sites"
    sites.mkdir()
    (sites / "apps.txt").write_text("frappe\nnewapp\n")

    callbacks._force_teardown(object(), tmp_path, "newapp")  # pip uninstall fails → ignored

    assert not (tmp_path / "apps" / "newapp").exists()
    assert (sites / "apps.txt").read_text() == "frappe\n"
