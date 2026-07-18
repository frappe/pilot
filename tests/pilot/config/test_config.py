import copy
from pathlib import Path

import pytest

from pilot.config import (
    BenchConfig,
    FirewallRule,
    ProductionConfig,
    WafCondition,
    WafRule,
)
from pilot.config.worker import WorkerGroup
from pilot.exceptions import ConfigError

FIXTURES_DIR = Path(__file__).parent.parent.parent / "fixtures"


def load_from_dict(data: dict) -> BenchConfig:
    config = BenchConfig._from_dict(data)
    config.validate()
    return config


MINIMAL_VALID_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
    "admin": {"domain": "admin.test.localhost"},
}


def test_load_minimal_config() -> None:
    config = BenchConfig.from_file(FIXTURES_DIR / "minimal.toml")

    assert config.name == "test-bench"
    assert config.python_version == "3.14"

    assert len(config.apps) == 1
    assert config.apps[0].name == "frappe"
    assert config.apps[0].repo == "https://github.com/frappe/frappe"
    assert config.apps[0].branch == "version-16"

    assert config.mariadb.root_password == "root"
    assert config.mariadb.host == "localhost"
    assert config.mariadb.port == 3306

    assert config.redis.cache_port == 13000
    assert config.redis.queue_port == 11000


def test_framework_app_is_first() -> None:
    config = BenchConfig.from_file(FIXTURES_DIR / "minimal.toml")
    assert config.framework_app.name == "frappe"


def test_framework_app_defaults_when_no_apps() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["apps"] = []
    config = BenchConfig._from_dict(data)
    assert config.framework_app.name == "frappe"


def test_app_by_name_found() -> None:
    config = BenchConfig.from_file(FIXTURES_DIR / "minimal.toml")
    app = config.get_app_by_name("frappe")
    assert app.name == "frappe"


def test_app_by_name_not_found() -> None:
    config = BenchConfig.from_file(FIXTURES_DIR / "minimal.toml")
    with pytest.raises(KeyError):
        config.get_app_by_name("nonexistent")


def test_config_without_apps_is_valid() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["apps"] = []
    config = load_from_dict(data)
    assert config.apps == []


def test_watch_apps_js_defaults_to_disabled() -> None:
    config = load_from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    assert config.watch_apps_js is False


def test_watch_apps_js_can_be_enabled() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["bench"]["watch_apps_js"] = True
    config = load_from_dict(data)
    assert config.watch_apps_js is True


def test_toml_writer_includes_watch_apps_js() -> None:
    config = BenchConfig._from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    config.watch_apps_js = True
    toml = config.dumps()
    assert "watch_apps_js = true" in toml


def test_reload_python_defaults_to_disabled() -> None:
    config = load_from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    assert config.reload_python is False


def test_reload_python_can_be_enabled() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["bench"]["reload_python"] = True
    config = load_from_dict(data)
    assert config.reload_python is True


def test_toml_writer_includes_reload_python() -> None:
    config = BenchConfig._from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    config.reload_python = True
    toml = config.dumps()
    assert "reload_python = true" in toml


def test_watch_admin_js_defaults_to_disabled() -> None:
    config = load_from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    assert config.watch_admin_js is False


def test_watch_admin_js_can_be_enabled() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["bench"]["watch_admin_js"] = True
    config = load_from_dict(data)
    assert config.watch_admin_js is True


def test_toml_writer_includes_watch_admin_js() -> None:
    config = BenchConfig._from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    config.watch_admin_js = True
    toml = config.dumps()
    assert "watch_admin_js = true" in toml


def test_rule_1_required_fields_bench_name_missing() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    del data["bench"]["name"]
    with pytest.raises(ConfigError) as exc_info:
        load_from_dict(data)
    assert "bench.name" in str(exc_info.value)


def test_rule_2_bench_name_invalid() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["bench"]["name"] = "123-invalid"
    with pytest.raises(ConfigError) as exc_info:
        load_from_dict(data)
    assert "bench.name" in str(exc_info.value)


def test_rule_4_duplicate_app_names() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["apps"].append(
        {"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}
    )
    with pytest.raises(ConfigError) as exc_info:
        load_from_dict(data)
    assert "frappe" in str(exc_info.value)
    assert "app" in str(exc_info.value).lower()


def test_rule_8_redis_ports_out_of_range() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["redis"]["cache_port"] = 500
    with pytest.raises(ConfigError) as exc_info:
        load_from_dict(data)
    assert "redis.cache_port" in str(exc_info.value)


def test_rule_8_redis_ports_must_be_distinct() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["redis"]["queue_port"] = 13000  # same as cache_port
    with pytest.raises(ConfigError) as exc_info:
        load_from_dict(data)
    assert "redis.cache_port" in str(exc_info.value) or "redis.queue_port" in str(exc_info.value)


def test_rule_9_worker_counts_must_be_positive() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["workers"] = [{"queues": ["default"], "count": 0}]
    with pytest.raises(ConfigError) as exc_info:
        load_from_dict(data)
    assert "workers[0].count" in str(exc_info.value)


def test_rule_11_invalid_letsencrypt_email() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["letsencrypt"] = {"email": "not-an-email"}
    with pytest.raises(ConfigError) as exc_info:
        load_from_dict(data)
    assert "letsencrypt.email" in str(exc_info.value)


def test_stale_mariadb_instance_key_is_ignored_not_a_hard_error() -> None:
    """Legacy MariaDB instance keys are ignored, not rejected."""
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["mariadb"]["instance"] = "old-bench"
    data["mariadb"]["version"] = "10.6"
    data["mariadb"]["data_dir"] = "/var/lib/mysql-old-bench"
    config = load_from_dict(data)
    assert not hasattr(config.mariadb, "instance")
    assert config.mariadb.root_password == "root"


def test_stale_postgres_instance_key_is_ignored_not_a_hard_error() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["postgres"] = {"instance": "old-bench", "version": "15", "root_password": "secret"}
    config = load_from_dict(data)
    assert not hasattr(config.postgres, "instance")
    assert config.postgres.root_password == "secret"


def test_redis_version_accepted() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["redis"]["version"] = "7"
    config = load_from_dict(data)
    assert config.redis.version == "7"


def test_redis_version_defaults_to_none() -> None:
    config = BenchConfig.from_file(FIXTURES_DIR / "minimal.toml")
    assert config.redis.version is None


def test_central_config_round_trips_through_typed_writer() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["central"] = {"endpoint": "https://central.test", "auth_token": "tok-123"}
    config = load_from_dict(data)
    toml = config.dumps()

    assert "[central]" in toml
    assert 'endpoint = "https://central.test"' in toml
    assert 'auth_token = "tok-123"' in toml


def test_postgres_defaults_when_section_absent() -> None:
    config = load_from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    assert config.postgres.host == "localhost"
    assert config.postgres.port == 5432
    assert config.postgres.admin_user == "postgres"
    assert config.postgres.root_password == ""


def test_postgres_section_roundtrip() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["postgres"] = {
        "host": "db.internal",
        "port": 5433,
        "admin_user": "pgroot",
        "root_password": "secret",
    }
    config = load_from_dict(data)
    assert config.postgres.host == "db.internal"
    assert config.postgres.port == 5433
    toml = config.dumps()
    assert "[postgres]" in toml
    assert 'host = "db.internal"' in toml
    assert "port = 5433" in toml
    assert 'admin_user = "pgroot"' in toml
    assert 'root_password = "secret"' in toml


def test_invalid_postgres_port_rejected() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["postgres"] = {"port": 0}
    with pytest.raises(ConfigError) as exc_info:
        load_from_dict(data)
    assert "postgres.port" in str(exc_info.value)


def test_invalid_redis_version() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["redis"]["version"] = "not-a-version"
    config = BenchConfig._from_dict(data)
    with pytest.raises(ConfigError) as exc_info:
        config.validate()
    assert "redis.version" in str(exc_info.value)


def test_branches_defaults_to_empty_list() -> None:
    config = BenchConfig.from_file(FIXTURES_DIR / "minimal.toml")
    assert config.apps[0].branches == []


def test_branches_parses_from_toml() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["apps"][0]["branch"] = "main"
    data["apps"][0]["branches"] = ["main", "develop"]
    config = load_from_dict(data)
    assert config.apps[0].branch == "main"
    assert config.apps[0].branches == ["main", "develop"]


def test_branches_active_branch_must_be_in_list() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["apps"][0]["branch"] = "version-16"
    data["apps"][0]["branches"] = ["main", "develop"]
    config = BenchConfig._from_dict(data)
    with pytest.raises(ConfigError) as exc_info:
        config.validate()
    assert "version-16" in str(exc_info.value)
    assert "branches" in str(exc_info.value)


def test_branches_active_branch_in_list_passes() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["apps"][0]["branch"] = "develop"
    data["apps"][0]["branches"] = ["main", "develop"]
    config = load_from_dict(data)
    assert config.apps[0].branch == "develop"
    assert config.apps[0].branches == ["main", "develop"]


def test_branches_single_branch_no_list_is_valid() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["apps"][0]["branch"] = "some-custom-branch"
    config = load_from_dict(data)
    assert config.apps[0].branch == "some-custom-branch"
    assert config.apps[0].branches == []


def test_production_defaults() -> None:
    p = ProductionConfig()
    assert p.process_manager == ""
    assert p.enabled is False


def test_production_parse_new_format_supervisor() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"enabled": True, "process_manager": "supervisor"}
    config = load_from_dict(data)
    assert config.production.process_manager == "supervisor"
    assert config.production.enabled is True


def test_production_parse_new_format_systemd() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"enabled": True, "process_manager": "systemd"}
    config = load_from_dict(data)
    assert config.production.process_manager == "systemd"
    assert config.production.enabled is True


def test_production_parse_new_format_disabled() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"enabled": False}
    config = load_from_dict(data)
    assert config.production.process_manager == ""
    assert config.production.enabled is False


def test_production_supervisord_alias_normalized() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"enabled": True, "process_manager": "supervisord"}
    config = load_from_dict(data)
    assert config.production.process_manager == "supervisor"


def test_production_legacy_process_manager_implies_enabled() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"process_manager": "supervisor", "nginx": True}
    config = load_from_dict(data)
    assert config.production.process_manager == "supervisor"
    assert config.production.enabled is True


def test_production_legacy_process_manager_none_disables() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"process_manager": "none"}
    config = load_from_dict(data)
    assert config.production.process_manager == ""
    assert config.production.enabled is False


def test_production_legacy_lightweight_systemd() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"enabled": True, "lightweight": True}
    config = load_from_dict(data)
    assert config.production.process_manager == "systemd"
    assert config.production.enabled is True


def test_production_missing_section_defaults() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    config = load_from_dict(data)
    assert config.production.process_manager == ""
    assert config.production.enabled is False


def test_production_enabled_requires_process_manager() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"enabled": True}
    data["admin"] = {"domain": "admin.example.com"}
    with pytest.raises(ConfigError):
        load_from_dict(data)


def test_toml_writer_production_emits_enabled_and_pm() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"enabled": True, "process_manager": "supervisor"}
    config = load_from_dict(data)
    toml = config.dumps()
    assert "enabled = true" in toml.split("[production]")[1].split("[")[0]
    assert 'process_manager = "supervisor"' in toml
    assert "nginx" not in toml.split("[production]")[1].split("[")[0]
    assert "lightweight" not in toml


def test_toml_writer_production_disabled_omits_pm() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    config = load_from_dict(data)
    section = config.dumps().split("[production]")[1].split("[")[0]
    assert "enabled = false" in section
    assert "process_manager" not in section


def test_admin_tls_roundtrip() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["admin"] = {"domain": "admin.example.com", "tls": False}
    config = load_from_dict(data)
    assert config.admin.tls is False
    assert "tls = false" in config.dumps()


def test_admin_allow_bench_management_defaults_to_true() -> None:
    config = load_from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    assert config.admin.allow_bench_management is True
    assert "allow_bench_management = true" in config.dumps()


def test_admin_allow_bench_management_can_be_disabled() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["admin"] = {"domain": "admin.example.com", "allow_bench_management": False}
    config = load_from_dict(data)
    assert config.admin.allow_bench_management is False
    assert "allow_bench_management = false" in config.dumps()


def test_admin_internal_port_is_port_plus_one() -> None:
    from pilot.config import AdminConfig

    assert AdminConfig(port=8002).internal_port == 8003
    assert AdminConfig(port=9100).internal_port == 9101


def test_db_type_defaults_to_mariadb() -> None:
    config = load_from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    assert config.db_type == "mariadb"


def test_db_type_postgres_roundtrip() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["bench"]["db_type"] = "postgres"
    config = load_from_dict(data)
    assert config.db_type == "postgres"
    assert 'db_type = "postgres"' in config.dumps()


def test_db_type_sqlite_roundtrip() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["bench"]["db_type"] = "sqlite"
    config = load_from_dict(data)
    assert config.db_type == "sqlite"
    assert 'db_type = "sqlite"' in config.dumps()


def test_invalid_db_type_rejected() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["bench"]["db_type"] = "mongodb"
    with pytest.raises(ConfigError) as exc_info:
        load_from_dict(data)
    assert "bench.db_type" in str(exc_info.value)


def test_monitor_defaults_when_section_absent() -> None:
    config = BenchConfig._from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    assert config.monitor.log_path is None
    assert config.monitor.system_log_path.name == "bench-system-stats.log"
    assert config.monitor.system_log_max_size == "500M"
    assert config.monitor.application_log_max_size == "500M"


def test_monitor_log_path_parsed_as_path() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["monitor"] = {"log_path": "/var/log/my-bench-stats.log"}
    config = BenchConfig._from_dict(data)
    from pathlib import Path

    assert config.monitor.log_path == Path("/var/log/my-bench-stats.log")


def test_monitor_log_path_absent_gives_none() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["monitor"] = {"system_log_max_size": "200M"}
    config = BenchConfig._from_dict(data)
    assert config.monitor.log_path is None


def test_monitor_custom_sizes_roundtrip() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"enabled": True, "process_manager": "systemd"}
    data["admin"] = {"domain": "admin.example.com"}
    data["monitor"] = {"system_log_max_size": "200M", "application_log_max_size": "100M"}
    config = BenchConfig._from_dict(data)
    assert config.monitor.system_log_max_size == "200M"
    assert config.monitor.application_log_max_size == "100M"
    toml = config.dumps()
    assert 'system_log_max_size = "200M"' in toml
    assert 'application_log_max_size = "100M"' in toml


def test_toml_writer_monitor_section_omitted_when_production_disabled() -> None:
    config = BenchConfig._from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    assert not config.production.enabled
    assert "[monitor]" not in config.dumps()


def test_toml_writer_monitor_section_emitted_when_production_enabled() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"enabled": True, "process_manager": "systemd"}
    data["admin"] = {"domain": "admin.example.com"}
    config = BenchConfig._from_dict(data)
    toml = config.dumps()
    assert "[monitor]" in toml
    assert "system_log_path" in toml
    assert "authority_file_path" in toml


def test_toml_writer_monitor_log_path_omitted_when_none() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"enabled": True, "process_manager": "systemd"}
    data["admin"] = {"domain": "admin.example.com"}
    config = BenchConfig._from_dict(data)
    assert config.monitor.log_path is None
    monitor_section = config.dumps().split("[monitor]")[1].split("[")[0]
    assert "\nlog_path =" not in monitor_section


def test_toml_writer_monitor_log_path_written_when_set() -> None:
    from pathlib import Path

    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["production"] = {"enabled": True, "process_manager": "systemd"}
    data["admin"] = {"domain": "admin.example.com"}
    config = BenchConfig._from_dict(data)
    config.monitor.log_path = Path("/var/log/test-bench-stats.log")
    toml = config.dumps()
    assert 'log_path = "/var/log/test-bench-stats.log"' in toml


def test_firewall_defaults_to_off_and_open() -> None:
    config = load_from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    assert config.firewall.enabled is False
    assert config.firewall.default == "allow"
    assert config.firewall.rules == []


def test_firewall_parses_rules() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["firewall"] = {
        "enabled": True,
        "default": "deny",
        "rules": [
            {"ip": "203.0.113.4", "action": "deny", "description": "bad"},
            {"ip": "2001:db8::/32", "action": "allow"},
        ],
    }
    config = load_from_dict(data)
    assert config.firewall.enabled is True
    assert config.firewall.default == "deny"
    assert [(r.ip, r.action) for r in config.firewall.rules] == [
        ("203.0.113.4", "deny"),
        ("2001:db8::/32", "allow"),
    ]


def test_firewall_accepts_ipv4_ipv6_and_cidr() -> None:
    for ip in ("203.0.113.4", "10.0.0.0/8", "2001:db8::1", "2001:db8::/32", "::1"):
        data = copy.deepcopy(MINIMAL_VALID_DATA)
        data["firewall"] = {"rules": [{"ip": ip, "action": "deny"}]}
        load_from_dict(data)  # must not raise


def test_firewall_rejects_bad_ip() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["firewall"] = {"rules": [{"ip": "not-an-ip", "action": "deny"}]}
    with pytest.raises(ConfigError):
        load_from_dict(data)


def test_firewall_rejects_bad_action_and_default() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["firewall"] = {"rules": [{"ip": "203.0.113.4", "action": "drop"}]}
    with pytest.raises(ConfigError):
        load_from_dict(data)
    data["firewall"] = {"default": "maybe", "rules": []}
    with pytest.raises(ConfigError):
        load_from_dict(data)


def test_firewall_toml_round_trip() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["firewall"] = {
        "enabled": True,
        "default": "deny",
        "rules": [{"ip": "203.0.113.4", "action": "deny", "description": "note"}],
    }
    config = load_from_dict(data)
    import tomllib

    reparsed = BenchConfig._from_dict(tomllib.loads(config.dumps()))
    reparsed.validate()
    assert reparsed.firewall.enabled is True
    assert reparsed.firewall.default == "deny"
    assert reparsed.firewall.rules[0].ip == "203.0.113.4"
    assert reparsed.firewall.rules[0].description == "note"


def test_firewall_section_omitted_when_off_and_empty() -> None:
    config = load_from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    assert "[firewall]" not in config.dumps()


def test_every_field_survives_a_round_trip(tmp_path: Path) -> None:
    """Regression guard: a field or section added to BenchConfig without being
    wired into both _from_dict and to_toml_dict should fail here immediately.

    nginx is deliberately excluded - it is fixed and never read from bench.toml.
    central.bootstrap_token is deliberately excluded - it is a one-time seed
    that is never serialized back out once consumed.
    """
    config = BenchConfig.default("roundtrip-bench")

    config.python_version = "3.13"
    config.http_port = 8123
    config.socketio_port = 9123
    config.socketio_backend = "python"
    config.watch_apps_js = True
    config.reload_python = True
    config.watch_admin_js = True
    config.db_type = "postgres"
    config.default_branch = "develop"
    config.apps[0].branches = ["version-16", "develop"]

    config.mariadb.host = "db.example.com"
    config.mariadb.port = 3307
    config.mariadb.root_password = "mariadb-secret"
    config.mariadb.admin_user = "custom_root"
    config.mariadb.socket_path = "/tmp/mariadb.sock"
    config.mariadb.existing = True

    config.postgres.host = "pg.example.com"
    config.postgres.port = 5433
    config.postgres.root_password = "postgres-secret"
    config.postgres.admin_user = "custom_pg"
    config.postgres.existing = True

    config.redis.cache_port = 13001
    config.redis.queue_port = 11001
    config.redis.version = "7.2"

    config.workers.groups = [WorkerGroup(queues=["default", "short"], count=3)]

    config.production.enabled = True
    config.production.process_manager = "systemd"
    config.production.use_companion_manager = True

    config.gunicorn.workers = 9
    config.gunicorn.threads = 17
    config.gunicorn.timeout = 121
    config.gunicorn.worker_class = "sync"
    config.gunicorn.malloc_arena_max = 4
    config.gunicorn.max_requests = 2001
    config.gunicorn.max_requests_jitter = 501

    config.letsencrypt.email = "ops@example.com"
    config.letsencrypt.webroot_path = Path("/custom/webroot")

    config.admin.port = 7001
    config.admin.timeout = 181
    config.admin.enabled = True
    config.admin.password = "admin-secret"
    config.admin.jwt_secret = "jwt-secret"
    config.admin.jwks_url = "https://issuer.example.com/jwks.json"
    config.admin.jwks_audience = "bench-fleet"
    config.admin.domain = "admin.example.com"
    config.admin.tls = True
    config.admin.allow_bench_management = False

    config.central.endpoint = "https://central.example.com"
    config.central.auth_token = "central-token"

    config.firewall.enabled = True
    config.firewall.default = "deny"
    config.firewall.rules = [FirewallRule(ip="203.0.113.4", action="deny", description="note")]

    config.waf.enabled = True
    config.waf.mode = "On"
    config.waf.paranoia = 2
    config.waf.inbound_threshold = 7
    config.waf.body_limit = "60m"
    config.waf.inspect_responses = True
    config.waf.exclusions = ["941100"]
    config.waf.exempt_paths = ["/health"]
    config.waf.custom_rules = [
        WafRule(
            name="block-bad-agent",
            action="block",
            match="all",
            enabled=True,
            conditions=[WafCondition(field="user_agent", operator="contains", value="badbot")],
        )
    ]

    config.s3.access_key = "AKIAEXAMPLE"
    config.s3.secret_key = "s3-secret"
    config.s3.bucket = "backups"
    config.s3.provider = "aws"
    config.s3.region = "us-east-1"

    config.monitor.system_log_path = Path("/var/log/custom-system.log")
    config.monitor.authority_file_path = Path("/var/log/.custom-authority")
    config.monitor.system_log_max_size = "600M"
    config.monitor.application_log_max_size = "700M"
    config.monitor.log_path = Path("/var/log/custom-app.log")

    path = tmp_path / "bench.toml"
    config.write(path)
    reloaded = BenchConfig.read(path)

    assert reloaded == config
