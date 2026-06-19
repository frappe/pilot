"""Tests for bench_cli.utils — write_toml serialiser."""
from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from bench_cli.utils import host_owner, normalize_host, write_toml


def _make_bench(benches: Path, name: str, *, admin_domain: str, sites: list[str] | None = None) -> Path:
    bench = benches / name
    (bench / "sites").mkdir(parents=True, exist_ok=True)
    (bench / "bench.toml").write_text(
        f'[bench]\nname = "{name}"\npython = "3.14"\n\n'
        '[[apps]]\nname = "frappe"\nrepo = "https://github.com/frappe/frappe"\nbranch = "version-16"\n\n'
        '[mariadb]\nroot_password = "root"\n\n'
        '[redis]\ncache_port = 13000\nqueue_port = 11000\n\n'
        f'[admin]\ndomain = "{admin_domain}"\n'
    )
    for site in sites or []:
        site_dir = bench / "sites" / site
        site_dir.mkdir(parents=True, exist_ok=True)
        (site_dir / "site_config.json").write_text("{}")
    return bench


def test_host_owner_detects_sibling_site(tmp_path: Path) -> None:
    benches = tmp_path / "benches"
    _make_bench(benches, "alpha", admin_domain="alpha-admin.localhost", sites=["shop.localhost"])
    assert host_owner(benches / "beta", "shop.localhost") == "alpha"


def test_host_owner_detects_sibling_admin_domain(tmp_path: Path) -> None:
    benches = tmp_path / "benches"
    _make_bench(benches, "alpha", admin_domain="admin.example.com")
    assert host_owner(benches / "beta", "admin.example.com") == "alpha"


def test_host_owner_free_host_returns_none(tmp_path: Path) -> None:
    benches = tmp_path / "benches"
    _make_bench(benches, "alpha", admin_domain="alpha-admin.localhost", sites=["shop.localhost"])
    assert host_owner(benches / "beta", "fresh.localhost") is None


def test_host_owner_ignores_self(tmp_path: Path) -> None:
    benches = tmp_path / "benches"
    bench = _make_bench(benches, "alpha", admin_domain="alpha-admin.localhost", sites=["shop.localhost"])
    # Scanning from alpha itself must not report alpha as the owner.
    assert host_owner(bench, "shop.localhost") is None


def test_host_owner_normalizes_case_and_trailing_dot(tmp_path: Path) -> None:
    benches = tmp_path / "benches"
    _make_bench(benches, "alpha", admin_domain="Admin.Example.COM")
    assert host_owner(benches / "beta", "admin.example.com.") == "alpha"


def test_host_owner_detects_site_alias(tmp_path: Path) -> None:
    benches = tmp_path / "benches"
    bench = _make_bench(benches, "alpha", admin_domain="alpha-admin.localhost", sites=["shop.localhost"])
    site_cfg = bench / "sites" / "shop.localhost" / "site_config.json"
    site_cfg.write_text('{"domains": ["www.shop.example.com"]}')
    assert host_owner(benches / "beta", "www.shop.example.com") == "alpha"


def test_normalize_host() -> None:
    assert normalize_host("Admin.Example.COM.") == "admin.example.com"
    assert normalize_host("") == ""
    assert normalize_host("  Foo.Local  ") == "foo.local"


def _roundtrip(tmp_path: Path, data: dict) -> dict:
    path = tmp_path / "out.toml"
    write_toml(path, data)
    with path.open("rb") as fh:
        return tomllib.load(fh)


def test_write_toml_scalars(tmp_path: Path) -> None:
    data = {"bench": {"name": "my-bench", "python": "3.14"}}
    result = _roundtrip(tmp_path, data)
    assert result["bench"]["name"] == "my-bench"
    assert result["bench"]["python"] == "3.14"


def test_write_toml_integer(tmp_path: Path) -> None:
    data = {"redis": {"cache_port": 13000}}
    result = _roundtrip(tmp_path, data)
    assert result["redis"]["cache_port"] == 13000


def test_write_toml_boolean(tmp_path: Path) -> None:
    data = {"admin": {"enabled": True}}
    result = _roundtrip(tmp_path, data)
    assert result["admin"]["enabled"] is True


def test_write_toml_list_of_strings(tmp_path: Path) -> None:
    data = {"sites": [{"name": "s1.localhost", "apps": ["frappe", "erpnext"]}]}
    result = _roundtrip(tmp_path, data)
    assert result["sites"][0]["apps"] == ["frappe", "erpnext"]


def test_write_toml_array_of_tables(tmp_path: Path) -> None:
    data = {
        "apps": [
            {"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "v16"},
            {"name": "erpnext", "repo": "https://github.com/frappe/erpnext", "branch": "v16"},
        ]
    }
    result = _roundtrip(tmp_path, data)
    assert len(result["apps"]) == 2
    assert result["apps"][0]["name"] == "frappe"
    assert result["apps"][1]["name"] == "erpnext"


def test_write_toml_preserves_new_app(tmp_path: Path) -> None:
    """Simulates get-app appending an entry to bench.toml."""
    original = {
        "bench": {"name": "test-bench", "python": "3.14"},
        "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "v16"}],
        "sites": [{"name": "site1.localhost", "apps": ["frappe"]}],
        "mariadb": {"root_password": "root"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
    }
    original["apps"].append(
        {"name": "payments", "repo": "https://github.com/frappe/payments", "branch": "v16"}
    )
    result = _roundtrip(tmp_path, original)
    names = [a["name"] for a in result["apps"]]
    assert "frappe" in names
    assert "payments" in names


def test_write_toml_file_is_valid_toml(tmp_path: Path) -> None:
    data = {
        "bench": {"name": "x", "python": "3.14"},
        "apps": [{"name": "frappe", "repo": "https://r.co/frappe", "branch": "main"}],
        "sites": [{"name": "s.localhost", "apps": ["frappe"]}],
        "mariadb": {"root_password": "r"},
        "redis": {"cache_port": 13000, "queue_port": 11000},
    }
    path = tmp_path / "bench.toml"
    write_toml(path, data)
    # If tomllib.load raises, the file is invalid TOML.
    with path.open("rb") as fh:
        tomllib.load(fh)
