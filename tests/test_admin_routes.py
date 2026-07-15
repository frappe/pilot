from collections import Counter
from pathlib import Path

from admin.backend.app import create_app


OPEN_ENDPOINTS = {"api_login", "api_logout", "api_ping", "api_status"}
SITE_SCOPED_ENDPOINTS = {
    "sites.add_domain",
    "sites.backup_site",
    "sites.clear_cache",
    "sites.delete_backup_schedule",
    "sites.detail",
    "sites.domain_dns_records",
    "sites.download_backup",
    "sites.drop_site",
    "sites.enable_ssl",
    "sites.force_drop_site",
    "sites.force_uninstall_app",
    "sites.get_and_install_app",
    "sites.get_backup_schedule",
    "sites.install_app",
    "sites.list_backups",
    "sites.list_domains",
    "sites.login_to_site",
    "sites.migrate_site",
    "sites.offsite_backup_urls",
    "sites.reinstall_site",
    "sites.remove_domain",
    "sites.set_backup_schedule",
    "sites.set_primary_domain",
    "sites.site_apps",
    "sites.uninstall_app",
    "sites.update_config",
}


def auth_policy(endpoint: str) -> str:
    if endpoint in OPEN_ENDPOINTS:
        return "open"
    if endpoint.startswith("setup."):
        return "setup-conditional"
    if endpoint in SITE_SCOPED_ENDPOINTS:
        return "authenticated+site-scope"
    if endpoint.startswith("benches."):
        return "authenticated+bench-management"
    return "authenticated"


def test_admin_route_inventory_matches_baseline(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    routes = [
        (
            ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"})),
            rule.rule,
            rule.endpoint,
            auth_policy(rule.endpoint),
        )
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith("/api")
    ]
    areas = [path.removeprefix("/api/").split("/", 1)[0] for _, path, _, _ in routes]

    assert len(routes) == 99
    assert len({(method, path) for method, path, _, _ in routes}) == 99
    assert Counter(method for method, _, _, _ in routes) == {
        "DELETE": 6,
        "GET": 49,
        "PATCH": 2,
        "POST": 42,
    }
    assert Counter(policy for _, _, _, policy in routes) == {
        "authenticated": 54,
        "authenticated+bench-management": 6,
        "authenticated+site-scope": 26,
        "open": 4,
        "setup-conditional": 9,
    }
    assert Counter(areas) == {
        "apps": 7,
        "benches": 6,
        "dashboard": 1,
        "database": 8,
        "git": 6,
        "login": 1,
        "logout": 1,
        "logs": 4,
        "monitor-history": 1,
        "monitor-status": 1,
        "ping": 1,
        "processes": 4,
        "settings": 4,
        "setup": 9,
        "sites": 30,
        "ssh-keys": 3,
        "stats": 1,
        "status": 1,
        "system-info": 1,
        "tasks": 7,
        "updates": 2,
    }
