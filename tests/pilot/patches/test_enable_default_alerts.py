from pathlib import Path

from pilot.config.alert_limit import DEFAULT_USAGE_LIMIT
from pilot.config.common import CommonConfig
from pilot.patches.enable_default_alerts import run


def _host(tmp_path: Path, **limits: int) -> Path:
    common = CommonConfig()
    for name, value in {"cpu_usage_limit": 0, "memory_usage_limit": 0, "disk_space_limit": 0, **limits}.items():
        setattr(common.resource_limits, name, value)
    common.write(tmp_path)
    return tmp_path


def _limits(benches_root: Path) -> tuple[int, int, int]:
    limits = CommonConfig.read(benches_root).resource_limits
    return limits.cpu_usage_limit, limits.memory_usage_limit, limits.disk_space_limit


def test_a_host_with_every_alert_off_gets_the_defaults(tmp_path: Path) -> None:
    benches_root = _host(tmp_path)

    run(benches_root)

    assert _limits(benches_root) == (DEFAULT_USAGE_LIMIT,) * 3


def test_a_host_that_set_any_limit_is_left_alone(tmp_path: Path) -> None:
    benches_root = _host(tmp_path, disk_space_limit=80)

    run(benches_root)

    assert _limits(benches_root) == (0, 0, 80)


def test_the_patch_runs_once_so_a_later_choice_to_turn_them_off_stays(tmp_path: Path) -> None:
    benches_root = _host(tmp_path)
    run(benches_root)
    _host(tmp_path)

    run(benches_root)

    assert _limits(benches_root) == (0, 0, 0)


def test_a_host_without_a_common_config_is_skipped(tmp_path: Path) -> None:
    run(tmp_path)

    assert not CommonConfig.path(tmp_path).exists()
