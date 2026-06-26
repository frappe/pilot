import copy
from pathlib import Path

import pytest

from pilot.config.bench_config import BenchConfig
from pilot.config.production_config import ProductionConfig
from pilot.config.toml_writer import bench_config_to_toml
from pilot.exceptions import ConfigError

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def load_from_dict(data: dict) -> BenchConfig:
    config = BenchConfig._from_dict(data)
    config.validate()
    return config


MINIMAL_VALID_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [
        {"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}
    ],
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
    app = config.app_by_name("frappe")
    assert app.name == "frappe"


def test_app_by_name_not_found() -> None:
    config = BenchConfig.from_file(FIXTURES_DIR / "minimal.toml")
    with pytest.raises(KeyError):
        config.app_by_name("nonexistent")


def test_config_without_apps_is_valid() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["apps"] = []
    config = load_from_dict(data)
    assert config.apps == []


# ── Validation rule tests ─────────────────────────────────────────────────────


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


# ── Dependency version tests ──────────────────────────────────────────────────


def test_mariadb_version_accepted() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["mariadb"]["version"] = "10.6"
    config = load_from_dict(data)
    assert config.mariadb.version == "10.6"


def test_redis_version_accepted() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["redis"]["version"] = "7"
    config = load_from_dict(data)
    assert config.redis.version == "7"


def test_mariadb_version_defaults_to_none() -> None:
    config = BenchConfig.from_file(FIXTURES_DIR / "minimal.toml")
    assert config.mariadb.version is None


def test_redis_version_defaults_to_none() -> None:
    config = BenchConfig.from_file(FIXTURES_DIR / "minimal.toml")
    assert config.redis.version is None


def test_invalid_mariadb_version() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["mariadb"]["version"] = "invalid"
    config = BenchConfig._from_dict(data)
    with pytest.raises(ConfigError) as exc_info:
        config.validate()
    assert "mariadb.version" in str(exc_info.value)


def test_mariadb_instance_defaults_to_shared() -> None:
    config = load_from_dict(copy.deepcopy(MINIMAL_VALID_DATA))
    assert config.mariadb.instance == ""
    assert config.mariadb.data_dir == ""


def test_mariadb_instance_and_data_dir_roundtrip() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["mariadb"]["instance"] = "test-bench"
    data["mariadb"]["data_dir"] = "/var/lib/mysql/test-bench"
    config = load_from_dict(data)
    assert config.mariadb.instance == "test-bench"
    assert config.mariadb.data_dir == "/var/lib/mysql/test-bench"
    # instance/data_dir survive serialization and only appear when set
    toml = bench_config_to_toml(config)
    assert 'instance = "test-bench"' in toml
    assert 'data_dir = "/var/lib/mysql/test-bench"' in toml


def test_mariadb_instance_omitted_from_toml_when_shared() -> None:
    toml = bench_config_to_toml(load_from_dict(copy.deepcopy(MINIMAL_VALID_DATA)))
    assert "instance =" not in toml
    assert "data_dir =" not in toml


def test_invalid_mariadb_instance_name() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["mariadb"]["instance"] = "1bad name"
    config = BenchConfig._from_dict(data)
    with pytest.raises(ConfigError) as exc_info:
        config.validate()
    assert "mariadb.instance" in str(exc_info.value)


def test_mariadb_data_dir_must_be_absolute() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["mariadb"]["instance"] = "test-bench"
    data["mariadb"]["data_dir"] = "relative/path"
    config = BenchConfig._from_dict(data)
    with pytest.raises(ConfigError) as exc_info:
        config.validate()
    assert "mariadb.data_dir" in str(exc_info.value)


def test_invalid_redis_version() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["redis"]["version"] = "not-a-version"
    config = BenchConfig._from_dict(data)
    with pytest.raises(ConfigError) as exc_info:
        config.validate()
    assert "redis.version" in str(exc_info.value)


# ── branches field tests ──────────────────────────────────────────────────────


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


# ── ProductionConfig tests ────────────────────────────────────────────────────


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
    toml = bench_config_to_toml(config)
    assert "enabled = true" in toml.split("[production]")[1].split("[")[0]
    assert 'process_manager = "supervisor"' in toml
    assert "nginx" not in toml.split("[production]")[1].split("[")[0]
    assert "lightweight" not in toml


def test_toml_writer_production_disabled_omits_pm() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    config = load_from_dict(data)
    section = bench_config_to_toml(config).split("[production]")[1].split("[")[0]
    assert "enabled = false" in section
    assert "process_manager" not in section


def test_admin_tls_roundtrip() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["admin"] = {"domain": "admin.example.com", "tls": False}
    config = load_from_dict(data)
    assert config.admin.tls is False
    assert "tls = false" in bench_config_to_toml(config)


# ── volume backing ────────────────────────────────────────────────────────────


def _data_with_volume(volume: dict) -> dict:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    data["volume"] = {"enabled": True, "pool": "bench-pool", **volume}
    return data


def test_volume_device_backing_valid() -> None:
    config = load_from_dict(_data_with_volume({"device": "/dev/sdb"}))
    assert config.volume.backing == "device"
    assert config.volume.device == "/dev/sdb"


def test_volume_device_backing_requires_device() -> None:
    with pytest.raises(ConfigError, match="volume.device is required"):
        load_from_dict(_data_with_volume({"backing": "device"}))


def test_volume_backing_inferred_from_device() -> None:
    config = load_from_dict(_data_with_volume({"device": "/dev/sdb"}))
    assert config.volume.backing == "device"


def test_volume_defaults_to_auto_backing() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    config = load_from_dict(data)  # no [volume] section at all
    assert config.volume.pool == "bench-pool"
    assert config.volume.backing == "auto"


def test_volume_image_backing_valid() -> None:
    config = load_from_dict(_data_with_volume({"backing": "image", "image": {"size": "60G"}}))
    assert config.volume.image.size == "60G"
    assert config.volume.image_path == "/var/lib/bench-zfs/bench-pool.img"


def test_volume_image_backing_requires_size() -> None:
    with pytest.raises(ConfigError, match="volume.image.size is required"):
        load_from_dict(_data_with_volume({"backing": "image"}))


def test_volume_image_path_must_be_absolute() -> None:
    with pytest.raises(ConfigError, match="must be an absolute path"):
        load_from_dict(_data_with_volume({"backing": "image", "image": {"size": "60G", "path": "relative/pool.img"}}))


def test_volume_image_custom_path_used() -> None:
    config = load_from_dict(_data_with_volume({"backing": "image", "image": {"size": "60G", "path": "/data/pool.img"}}))
    assert config.volume.image_path == "/data/pool.img"


def test_volume_auto_backing_requires_no_backing_fields() -> None:
    config = load_from_dict(_data_with_volume({"backing": "auto"}))
    assert config.volume.backing == "auto"


def test_volume_invalid_backing_rejected() -> None:
    with pytest.raises(ConfigError, match="Must be 'device', 'image', or 'auto'"):
        load_from_dict(_data_with_volume({"backing": "loopback"}))


def test_volume_reservation_cannot_exceed_quota() -> None:
    with pytest.raises(ConfigError, match="cannot exceed quota"):
        load_from_dict(_data_with_volume({"device": "/dev/sdb", "dataset": {"reservation": "20G", "quota": "10G"}}))


def test_volume_skipped_when_not_configured() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)  # no [volume] section
    config = BenchConfig._from_dict(data)
    config.validate()  # must not raise — ZFS validation is skipped when volume not configured
    assert not config.volume.enabled


def test_toml_writer_omits_volume_when_not_configured() -> None:
    data = copy.deepcopy(MINIMAL_VALID_DATA)
    config = load_from_dict(data)
    toml = bench_config_to_toml(config)
    assert "[volume]" not in toml


def test_toml_writer_volume_image_backing_round_trip() -> None:
    config = load_from_dict(_data_with_volume({"backing": "image", "image": {"size": "60G", "path": "/data/pool.img"}}))
    toml = bench_config_to_toml(config)
    assert 'backing = "image"' in toml
    assert '[volume.image]' in toml
    assert 'size = "60G"' in toml
    assert 'path = "/data/pool.img"' in toml
    assert 'device = ' not in toml.split("[volume]")[1]


def test_toml_writer_volume_device_backing() -> None:
    config = load_from_dict(_data_with_volume({"device": "/dev/sdb"}))
    toml = bench_config_to_toml(config)
    assert 'backing = "device"' in toml
    assert 'device = "/dev/sdb"' in toml
    assert "[volume.image]" not in toml


def test_admin_internal_port_is_port_plus_one() -> None:
    from pilot.config.admin_config import AdminConfig

    assert AdminConfig(port=8002).internal_port == 8003
    assert AdminConfig(port=9100).internal_port == 9101
