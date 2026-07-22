from collections import Counter
from pathlib import Path

from admin.backend.api.routes import API_ROOT_PREFIX, API_V1_PREFIX
from admin.backend.app import create_app
from admin.backend.middleware import AuthPolicy, get_auth_policy

SITE_SCOPED_ENDPOINTS = {
    "sites.add_domain",
    "sites.backup_site",
    "sites.central_proxy",
    "sites.clear_cache",
    "sites.delete_backup_schedule",
    "sites.backup_download_links",
    "sites.delete_site_app",
    "sites.detail",
    "sites.domain_dns_records",
    "sites.download_backup_file",
    "sites.drop_site",
    "sites.enable_tls",
    "sites.get_backup",
    "sites.get_backup_schedule",
    "sites.get_configuration",
    "sites.get_domain",
    "sites.install_site_app",
    "sites.get_monitoring",
    "sites.get_uptime",
    "sites.list_backups",
    "sites.list_domains",
    "sites.migrate_site",
    "sites.reinstall_site",
    "sites.remove_domain",
    "sites.set_backup_schedule",
    "sites.site_apps",
    "sites.update_configuration",
    "sites.update_domain",
    "sites.create_login_link",
}


def auth_policy(app, endpoint: str) -> str:
    view = app.view_functions[endpoint]
    policy = get_auth_policy(view)
    if policy != AuthPolicy.AUTHENTICATED:
        return policy.value
    if endpoint in SITE_SCOPED_ENDPOINTS:
        return "authenticated+site-scope"
    if endpoint.startswith(("benches.", "bench-readiness.")):
        return "authenticated+bench-management"
    return "authenticated"


def test_admin_route_inventory_matches_baseline(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    routes = [
        (
            ",".join(sorted(rule.methods - {"HEAD", "OPTIONS"})),
            rule.rule,
            rule.endpoint,
            auth_policy(app, rule.endpoint),
        )
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith(API_V1_PREFIX)
    ]
    areas = [path.removeprefix(f"{API_V1_PREFIX}/").split("/", 1)[0] for _, path, _, _ in routes]
    unversioned = [
        rule.rule
        for rule in app.url_map.iter_rules()
        if rule.rule.startswith(f"{API_ROOT_PREFIX}/") and not rule.rule.startswith(f"{API_V1_PREFIX}/")
    ]

    assert len(routes) == 146
    assert unversioned == []
    assert len({(method, path) for method, path, _, _ in routes}) == 146
    assert Counter(method for method, _, _, _ in routes) == {
        "DELETE": 11,
        "GET": 76,
        "PATCH": 4,
        "POST": 50,
        "PUT": 5,
    }
    assert Counter(policy for _, _, _, policy in routes) == {
        "authenticated": 96,
        "authenticated+bench-management": 9,
        "authenticated+site-scope": 30,
        "open": 5,
        "setup-conditional": 6,
    }
    assert Counter(areas) == {
        "apps": 6,
        "app-update-checks": 1,
        "app-updates": 1,
        "audit-events": 1,
        "bench-readiness-checks": 1,
        "benches": 8,
        "cli-update-checks": 1,
        "cli-updates": 1,
        "database": 9,
        "editor": 30,
        "git": 6,
        "bootstrap": 1,
        "health": 1,
        "logs": 4,
        "marketplace": 1,
        "migrations": 6,
        "monitor": 2,
        "network": 1,
        "updates": 1,
        "runtime": 4,
        "settings": 2,
        "setup": 6,
        "sites": 33,
        "ssh-keys": 3,
        "metrics": 1,
        "session": 3,
        "system": 1,
        "task-worker": 3,
        "tasks": 7,
        "waf": 1,
    }

    route_keys = {(method, path) for method, path, _, _ in routes}
    assert {
        ("GET", "/api/v1/benches"),
        ("POST", "/api/v1/benches"),
        ("GET", "/api/v1/benches/<name>"),
        ("DELETE", "/api/v1/benches/<name>"),
        ("POST", "/api/v1/benches/<name>/actions/start"),
        ("POST", "/api/v1/benches/<name>/actions/stop"),
        ("POST", "/api/v1/benches/<name>/actions/restart"),
        ("GET", "/api/v1/benches/domain-options"),
        ("POST", "/api/v1/bench-readiness-checks"),
    } <= route_keys
    assert {
        ("GET", "/api/v1/apps"),
        ("POST", "/api/v1/apps"),
        ("GET", "/api/v1/apps/<name>"),
        ("PATCH", "/api/v1/apps/<name>"),
        ("DELETE", "/api/v1/apps/<name>"),
        ("GET", "/api/v1/marketplace/apps"),
        ("GET", "/api/v1/app-updates"),
        ("POST", "/api/v1/app-update-checks"),
    } <= route_keys
    assert {
        ("GET", "/api/v1/sites"),
        ("POST", "/api/v1/sites"),
        ("GET", "/api/v1/sites/<name>"),
        ("DELETE", "/api/v1/sites/<name>"),
        ("POST", "/api/v1/sites/<name>/actions/reinstall"),
        ("POST", "/api/v1/sites/<name>/actions/clear-cache"),
        ("POST", "/api/v1/sites/<name>/actions/migrate"),
        ("POST", "/api/v1/sites/<name>/actions/enable-tls"),
        ("GET", "/api/v1/sites/<name>/configuration"),
        ("PATCH", "/api/v1/sites/<name>/configuration"),
        ("GET", "/api/v1/sites/<name>/apps"),
        ("POST", "/api/v1/sites/<name>/apps"),
        ("DELETE", "/api/v1/sites/<name>/apps/<app>"),
        ("GET", "/api/v1/sites/<name>/domains"),
        ("POST", "/api/v1/sites/<name>/domains"),
        ("GET", "/api/v1/sites/<name>/domains/<domain>"),
        ("PATCH", "/api/v1/sites/<name>/domains/<domain>"),
        ("DELETE", "/api/v1/sites/<name>/domains/<domain>"),
        ("GET", "/api/v1/sites/<name>/domains/<domain>/dns-records"),
        ("GET", "/api/v1/sites/<name>/backups"),
        ("POST", "/api/v1/sites/<name>/backups"),
        ("GET", "/api/v1/sites/<name>/backups/<timestamp>"),
        ("GET", "/api/v1/sites/<name>/backups/<timestamp>/files/<file_id>/content"),
        ("GET", "/api/v1/sites/<name>/backups/<timestamp>/download-links"),
        ("GET", "/api/v1/sites/<name>/backup-schedule"),
        ("PUT", "/api/v1/sites/<name>/backup-schedule"),
        ("DELETE", "/api/v1/sites/<name>/backup-schedule"),
        ("GET", "/api/v1/sites/<name>/central/<path:method_path>"),
        ("POST", "/api/v1/sites/<name>/central/<path:method_path>"),
    } <= route_keys
    assert {
        ("GET", "/api/v1/tasks"),
        ("POST", "/api/v1/tasks"),
        ("GET", "/api/v1/tasks/<task_id>"),
        ("DELETE", "/api/v1/tasks/<task_id>"),
        ("POST", "/api/v1/tasks/<task_id>/actions/retry"),
        ("GET", "/api/v1/tasks/<task_id>/events"),
        ("GET", "/api/v1/tasks/<task_id>/output/content"),
        ("GET", "/api/v1/task-worker"),
        ("POST", "/api/v1/task-worker/actions/start"),
        ("POST", "/api/v1/task-worker/actions/stop"),
    } <= route_keys
    assert {
        ("GET", "/api/v1/runtime/processes"),
        ("POST", "/api/v1/runtime/actions/start"),
        ("POST", "/api/v1/runtime/actions/stop"),
        ("POST", "/api/v1/runtime/actions/restart"),
        ("GET", "/api/v1/logs"),
        ("GET", "/api/v1/logs/<filename>"),
        ("GET", "/api/v1/logs/<filename>/events"),
        ("GET", "/api/v1/logs/<filename>/content"),
    } <= route_keys
    assert {
        ("GET", "/api/v1/git/connection"),
        ("PUT", "/api/v1/git/connection"),
        ("DELETE", "/api/v1/git/connection"),
        ("GET", "/api/v1/git/repositories"),
        ("GET", "/api/v1/git/branches"),
        ("POST", "/api/v1/git/repository-resolutions"),
    } <= route_keys
    assert {
        ("GET", "/api/v1/settings"),
        ("PATCH", "/api/v1/settings"),
        ("GET", "/api/v1/audit-events"),
        ("GET", "/api/v1/network/client"),
        ("GET", "/api/v1/ssh-keys"),
        ("POST", "/api/v1/ssh-keys"),
        ("DELETE", "/api/v1/ssh-keys/<fingerprint>"),
        ("GET", "/api/v1/monitor/status"),
        ("GET", "/api/v1/monitor/history"),
        ("GET", "/api/v1/system"),
        ("GET", "/api/v1/metrics"),
        ("GET", "/api/v1/cli-updates"),
        ("POST", "/api/v1/cli-update-checks"),
    } <= route_keys
    assert {
        ("GET", "/api/v1/setup/configuration"),
        ("PUT", "/api/v1/setup/configuration"),
        ("GET", "/api/v1/setup/framework-branches"),
        ("POST", "/api/v1/setup/database-validations"),
        ("POST", "/api/v1/setup/actions/start"),
        ("POST", "/api/v1/setup/actions/finish"),
    } <= route_keys
    assert not {
        path
        for _, path, _, _ in routes
        if path
        in {
            "/api/v1/tasks/",
            "/api/v1/tasks/run",
            "/api/v1/tasks/<task_id>/kill",
            "/api/v1/tasks/<task_id>/rerun",
            "/api/v1/tasks/<task_id>/stream",
            "/api/v1/tasks/<task_id>/output/download",
            "/api/v1/setup/stream/<task_id>",
            "/api/v1/setup/config",
            "/api/v1/setup/framework_branches",
            "/api/v1/setup/save",
            "/api/v1/setup/validate-mariadb",
            "/api/v1/setup/validate-postgres",
            "/api/v1/setup/start",
            "/api/v1/setup/finish",
            "/api/v1/benches/",
            "/api/v1/benches/new",
            "/api/v1/benches/wildcard-domains",
            "/api/v1/benches/ready",
            "/api/v1/benches/<name>/actions/<action_name>",
            "/api/v1/sites/",
            "/api/v1/sites/create",
            "/api/v1/sites/create-from-upload",
            "/api/v1/sites/<name>/drop",
            "/api/v1/sites/<name>/force-drop",
            "/api/v1/sites/<name>/reinstall",
            "/api/v1/sites/<name>/clear-cache",
            "/api/v1/sites/<name>/migrate",
            "/api/v1/sites/<name>/enable-ssl",
            "/api/v1/sites/<name>/config",
            "/api/v1/sites/<name>/install-app",
            "/api/v1/sites/<name>/get-and-install-app",
            "/api/v1/sites/<name>/uninstall-app",
            "/api/v1/sites/<name>/force-uninstall-app",
            "/api/v1/sites/<name>/domains/primary",
            "/api/v1/sites/<name>/domains/dns-records",
            "/api/v1/sites/<name>/backup",
            "/api/v1/sites/<name>/backups/download",
            "/api/v1/sites/<name>/backups/<timestamp>/offsite-urls",
            "/api/v1/apps/",
            "/api/v1/apps/marketplace",
            "/api/v1/apps/add",
            "/api/v1/apps/get-and-install",
            "/api/v1/apps/<name>/remove",
            "/api/v1/apps/<name>/set-upstream",
            "/api/v1/updates/",
            "/api/v1/processes/",
            "/api/v1/processes/start",
            "/api/v1/processes/stop",
            "/api/v1/processes/restart",
            "/api/v1/logs/",
            "/api/v1/logs/<filename>/download",
            "/api/v1/logs/<filename>/stream",
            "/api/v1/git/integration",
            "/api/v1/git/repos",
            "/api/v1/git/resolve",
            "/api/v1/settings/",
            "/api/v1/settings/audit/log",
            "/api/v1/settings/my-ip",
            "/api/v1/ssh-keys/",
            "/api/v1/monitor-status",
            "/api/v1/monitor-history",
            "/api/v1/system-info",
            "/api/v1/stats",
            "/api/v1/updates/cli",
            "/api/v1/setup/new-site",
        }
    }
