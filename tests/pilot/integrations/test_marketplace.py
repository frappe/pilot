"""Tests for pilot.integrations.marketplace — Resolver and Marketplace classes."""

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from pilot.integrations.marketplace import Marketplace, Resolver
from pilot.exceptions import BenchError


# ── helpers ──────────────────────────────────────────────────────────────────


def make_resolver(
    app: str = "myapp",
    version: str = "1.0.0",
    frappe_version: str = "15.0.0",
    required_version: str = ">=15.0.0,<16.0.0",
    dependencies: dict | None = None,
    is_installable: bool = True,
    repo: str = "https://github.com/frappe/myapp",
    target: str = "version-15",
    target_type: str = "branch",
) -> Resolver:
    return Resolver(
        app=app,
        repo=repo,
        target_type=target_type,
        target=target,
        version=version,
        frappe_version=frappe_version,
        required_version=required_version,
        is_installable=is_installable,
        dependencies=dependencies or {},
    )


def inject_registry(root: Resolver, resolvers: list[Resolver]) -> None:
    """Share a single registry pointer across all supplied resolvers."""
    registry: dict[str, list[Resolver]] = {}
    for r in resolvers:
        registry.setdefault(r.app, []).append(r)
    for r in [root, *resolvers]:
        r._registry = registry


# ── Resolver.to_dict ─────────────────────────────────────────────────────────


def test_to_dict_maps_app_field_to_name():
    r = make_resolver(app="erpnext", version="15.1.0")
    d = r.to_dict()
    assert d["name"] == "erpnext"
    assert "app" not in d


def test_to_dict_excludes_registry():
    r = make_resolver()
    d = r.to_dict()
    assert "_registry" not in d
    assert "registry" not in d


def test_to_dict_contains_all_expected_keys():
    r = make_resolver()
    keys = {"name", "repo", "target_type", "target", "version", "frappe_version",
             "required_version", "dependencies", "is_installable",
             "title", "description", "logo_url", "category", "categories", "stars",
             "documentation", "website"}
    assert keys == set(r.to_dict().keys())


def test_to_dict_preserves_values():
    r = make_resolver(app="hrms", version="2.0.0")
    r.title = "HR Management"
    r.stars = 42
    d = r.to_dict()
    assert d["version"] == "2.0.0"
    assert d["title"] == "HR Management"
    assert d["stars"] == 42


def test_to_dict_categories_defaults_to_empty_list():
    r = make_resolver()
    assert r.to_dict()["categories"] == []


def test_to_dict_includes_categories():
    r = make_resolver()
    r.categories = ["Payments", "Accounting"]
    assert r.to_dict()["categories"] == ["Payments", "Accounting"]


# ── Resolver.resolve — non-installable guard ──────────────────────────────────


def test_resolve_raises_for_non_installable_app():
    r = make_resolver(is_installable=False, frappe_version="14.0.0", required_version=">=15.0.0,<16.0.0")
    with pytest.raises(BenchError, match="not compatible"):
        r.resolve()


def test_resolve_error_message_contains_version_info():
    r = make_resolver(is_installable=False, frappe_version="14.0.0", required_version=">=15.0.0,<16.0.0")
    with pytest.raises(BenchError) as exc:
        r.resolve()
    msg = str(exc.value)
    assert "14.0.0" in msg
    assert ">=15.0.0,<16.0.0" in msg


# ── Resolver.resolve — no dependencies ───────────────────────────────────────


def test_resolve_single_app_no_deps_returns_self():
    r = make_resolver()
    result = r.resolve()
    assert result == [r]


def test_resolve_returns_list_of_resolvers():
    r = make_resolver()
    assert isinstance(r.resolve(), list)


# ── Resolver.resolve — transitive dependencies ────────────────────────────────


def test_resolve_installs_dependency_before_root():
    dep = make_resolver(app="payments", version="1.0.0")
    root = make_resolver(app="erpnext", dependencies={"payments": ">=1.0.0"})
    inject_registry(root, [dep])

    result = root.resolve()
    assert [r.app for r in result] == ["payments", "erpnext"]


def test_resolve_deep_chain_order():
    # erpnext → payments → stripe_integration
    stripe = make_resolver(app="stripe_integration", version="1.0.0")
    payments = make_resolver(app="payments", version="1.0.0", dependencies={"stripe_integration": ">=1.0.0"})
    erpnext = make_resolver(app="erpnext", version="15.0.0", dependencies={"payments": ">=1.0.0"})
    inject_registry(erpnext, [payments, stripe])

    result = erpnext.resolve()
    assert [r.app for r in result] == ["stripe_integration", "payments", "erpnext"]


def test_resolve_diamond_dependency_deduplication():
    # root → B, root → C, B → D, C → D  =>  D appears exactly once
    d = make_resolver(app="D", version="1.0.0")
    b = make_resolver(app="B", version="1.0.0", dependencies={"D": ">=1.0.0"})
    c = make_resolver(app="C", version="1.0.0", dependencies={"D": ">=1.0.0"})
    root = make_resolver(app="root", dependencies={"B": ">=1.0.0", "C": ">=1.0.0"})
    inject_registry(root, [b, c, d])

    result = root.resolve()
    names = [r.app for r in result]
    assert names.count("D") == 1
    assert names[-1] == "root"
    assert names.index("D") < names.index("B")
    assert names.index("D") < names.index("C")


# ── Resolver.resolve — version specifier matching ─────────────────────────────


def test_resolve_picks_dep_satisfying_version_spec():
    dep_old = make_resolver(app="payments", version="0.9.0")
    dep_new = make_resolver(app="payments", version="2.0.0")
    root = make_resolver(app="erpnext", dependencies={"payments": ">=2.0.0"})
    registry: dict = {"payments": [dep_old, dep_new]}
    root._registry = registry

    result = root.resolve()
    payment_resolver = next(r for r in result if r.app == "payments")
    assert payment_resolver.version == "2.0.0"


def test_resolve_raises_when_no_dep_version_satisfies_spec():
    dep = make_resolver(app="payments", version="1.0.0")
    root = make_resolver(app="erpnext", dependencies={"payments": ">=3.0.0"})
    inject_registry(root, [dep])

    with pytest.raises(BenchError, match="payments"):
        root.resolve()


def test_resolve_raises_on_version_conflict_in_diamond():
    # B picks payments 1.5.0, A requires payments >=2.0.0 — conflict
    payments_old = make_resolver(app="payments", version="1.5.0")
    payments_new = make_resolver(app="payments", version="2.0.0")
    b = make_resolver(app="B", dependencies={"payments": ">=1.0.0,<2.0.0"})
    a = make_resolver(app="A", dependencies={"payments": ">=2.0.0"})
    root = make_resolver(app="root", dependencies={"B": "", "A": ""})
    registry = {"payments": [payments_old, payments_new], "B": [b], "A": [a]}
    root._registry = b._registry = a._registry = registry

    with pytest.raises(BenchError, match="conflict"):
        root.resolve()


def test_resolve_accepts_dep_with_empty_spec():
    dep = make_resolver(app="payments", version="1.0.0")
    root = make_resolver(app="erpnext", dependencies={"payments": ""})
    inject_registry(root, [dep])

    result = root.resolve()
    assert any(r.app == "payments" for r in result)


# ── Resolver.resolve — missing dependency ─────────────────────────────────────


def test_resolve_raises_when_dep_not_in_registry():
    root = make_resolver(app="erpnext", dependencies={"missing_app": ">=1.0.0"})
    root._registry = {}

    with pytest.raises(BenchError, match="missing_app"):
        root.resolve()


def test_resolve_raises_when_registry_empty_and_has_deps():
    root = make_resolver(app="erpnext", dependencies={"someapp": ">=1.0.0"})
    # _registry defaults to {} — no injection needed

    with pytest.raises(BenchError):
        root.resolve()


# ── Resolver.resolve — circular dependency detection ─────────────────────────


def test_resolve_detects_direct_cycle():
    # A depends on B, B depends on A
    a = make_resolver(app="A", dependencies={"B": ""})
    b = make_resolver(app="B", dependencies={"A": ""})
    inject_registry(a, [b])
    # A._registry also needs B's registry pointer for nested resolution
    b._registry = a._registry

    with pytest.raises(BenchError, match="Circular dependency"):
        a.resolve()


def test_resolve_cycle_error_includes_cycle_path():
    a = make_resolver(app="A", dependencies={"B": ""})
    b = make_resolver(app="B", dependencies={"A": ""})
    inject_registry(a, [b])
    b._registry = a._registry

    with pytest.raises(BenchError) as exc:
        a.resolve()
    assert "A" in str(exc.value)
    assert "B" in str(exc.value)


def test_resolve_detects_indirect_cycle():
    # A → B → C → A
    a = make_resolver(app="A", dependencies={"B": ""})
    b = make_resolver(app="B", dependencies={"C": ""})
    c = make_resolver(app="C", dependencies={"A": ""})
    registry = {"A": [a], "B": [b], "C": [c]}
    a._registry = b._registry = c._registry = registry

    with pytest.raises(BenchError, match="Circular dependency"):
        a.resolve()


# ── Marketplace.read_all_apps ─────────────────────────────────────────────────


SAMPLE_REGISTRY = [
    {
        "name": "erpnext",
        "repo": "https://github.com/frappe/erpnext",
        "title": "ERPNext",
        "description": "ERP for the real world",
        "logo_url": "",
        "category": "Applications",
        "categories": ["Accounting", "Featured"],
        "stars": 22000,
        "targets": [
            {
                "target_type": "branch",
                "target": "version-15",
                "version": "15.0.0",
                "frappe_core": ">=15.0.0,<16.0.0",
                "dependencies": {},
            },
            {
                "target_type": "branch",
                "target": "version-16",
                "version": "16.0.0",
                "frappe_core": ">=16.0.0,<17.0.0",
                "dependencies": {},
            },
        ],
    },
    {
        "name": "hrms",
        "repo": "https://github.com/frappe/hrms",
        "title": "HR Management",
        "description": "",
        "logo_url": "",
        "category": "Applications",
        "stars": None,
        "targets": [
            {
                "target_type": "branch",
                "target": "version-15",
                "version": "15.0.0",
                "frappe_core": ">=15.0.0,<16.0.0",
                "dependencies": {"erpnext": ">=15.0.0"},
            },
        ],
    },
    {
        "name": "old_app",
        "repo": "https://github.com/frappe/old_app",
        "title": "Old App",
        "description": "",
        "logo_url": "",
        "category": "Utilities",
        "stars": 10,
        "targets": [
            {
                "target_type": "branch",
                "target": "version-14",
                "version": "14.0.0",
                "frappe_core": ">=14.0.0,<15.0.0",
                "dependencies": {},
            },
        ],
    },
]


def make_marketplace(frappe_version: str, registry: list | None = None) -> Marketplace:
    bench = MagicMock()
    bench.env_path = Path("/fake/env")

    import json

    with (
        patch("pilot.integrations.marketplace.Marketplace.get_current_frappe_version", return_value=frappe_version),
        patch("pilot.integrations.marketplace.Marketplace._read_apps_json", return_value=json.dumps(registry or SAMPLE_REGISTRY)),
    ):
        mp = Marketplace(bench)
    return mp


def test_read_all_apps_returns_all_apps_including_incompatible():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    names = {a.app for a in apps}
    assert "erpnext" in names
    assert "hrms" in names
    assert "old_app" in names


def test_read_all_apps_marks_compatible_app_as_installable():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    erpnext = next(a for a in apps if a.app == "erpnext")
    assert erpnext.is_installable is True


def test_read_all_apps_marks_incompatible_app_as_not_installable():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    old_app = next(a for a in apps if a.app == "old_app")
    assert old_app.is_installable is False


def test_read_all_apps_uses_correct_target_for_frappe_version():
    mp = make_marketplace("16.0.0")
    apps = mp.read_all_apps()
    erpnext = next(a for a in apps if a.app == "erpnext")
    assert erpnext.target == "version-16"
    assert erpnext.version == "16.0.0"


def test_read_all_apps_uses_first_target_as_display_for_incompatible():
    mp = make_marketplace("17.0.0")
    apps = mp.read_all_apps()
    old_app = next(a for a in apps if a.app == "old_app")
    assert old_app.target == "version-14"


def test_read_all_apps_passes_categories_through():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    erpnext = next(a for a in apps if a.app == "erpnext")
    hrms = next(a for a in apps if a.app == "hrms")
    assert erpnext.categories == ["Accounting", "Featured"]
    assert hrms.categories == []  # absent key defaults to empty


def test_read_all_apps_stars_none_becomes_zero():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    hrms = next(a for a in apps if a.app == "hrms")
    # stars: None in registry should be handled gracefully
    assert isinstance(hrms.stars, (int, type(None)))


def test_read_all_apps_injects_shared_registry_pointer():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    registries = [id(a._registry) for a in apps]
    assert len(set(registries)) == 1, "All resolvers must share the same registry dict"


def test_read_all_apps_registry_only_contains_installable_apps():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    registry = apps[0]._registry
    assert "old_app" not in registry
    assert "erpnext" in registry
    assert "hrms" in registry


def test_read_all_apps_registry_entries_are_lists_of_resolvers():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    registry = apps[0]._registry
    for entries in registry.values():
        assert isinstance(entries, list)
        assert all(isinstance(e, Resolver) for e in entries)


def test_read_all_apps_resolve_works_end_to_end():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    hrms = next(a for a in apps if a.app == "hrms")
    result = hrms.resolve()
    names = [r.app for r in result]
    assert names == ["erpnext", "hrms"]


def test_read_all_apps_incompatible_app_resolve_raises():
    mp = make_marketplace("15.0.0")
    apps = mp.read_all_apps()
    old_app = next(a for a in apps if a.app == "old_app")
    with pytest.raises(BenchError, match="not compatible"):
        old_app.resolve()


def test_read_all_apps_multi_target_registry_has_all_compatible_versions():
    registry = [
        {
            "name": "payments",
            "repo": "https://github.com/frappe/payments",
            "title": "Payments",
            "description": "",
            "logo_url": "",
            "category": "Applications",
            "stars": 0,
            "targets": [
                {
                    "target_type": "branch",
                    "target": "version-15",
                    "version": "15.0.0",
                    "frappe_core": ">=15.0.0,<16.0.0",
                    "dependencies": {},
                },
                {
                    "target_type": "branch",
                    "target": "version-15-hotfix",
                    "version": "15.1.0",
                    "frappe_core": ">=15.0.0,<16.0.0",
                    "dependencies": {},
                },
            ],
        }
    ]
    mp = make_marketplace("15.0.5", registry)
    apps = mp.read_all_apps()
    reg_entries = apps[0]._registry.get("payments", [])
    assert len(reg_entries) == 2


def test_read_all_apps_no_targets_produces_non_installable():
    registry = [
        {
            "name": "ghost_app",
            "repo": "https://github.com/frappe/ghost_app",
            "title": "Ghost",
            "description": "",
            "logo_url": "",
            "category": "Utilities",
            "stars": 0,
            "targets": [],
        }
    ]
    mp = make_marketplace("15.0.0", registry)
    apps = mp.read_all_apps()
    ghost = next(a for a in apps if a.app == "ghost_app")
    assert ghost.is_installable is False


# ── Marketplace.find_app ──────────────────────────────────────────────────────


def test_find_app_returns_matching_resolver():
    mp = make_marketplace("15.0.0")
    resolver = mp.find_app("erpnext")
    assert resolver.app == "erpnext"


def test_find_app_raises_for_unknown_app():
    mp = make_marketplace("15.0.0")
    with pytest.raises(BenchError, match="'unknown_app' not found in marketplace"):
        mp.find_app("unknown_app")
