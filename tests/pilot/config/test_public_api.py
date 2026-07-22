from __future__ import annotations

import pilot.config as config


def test_config_package_exports_public_types() -> None:
    assert config.BenchConfig.__module__ == "pilot.config.bench"
    assert config.MariaDBConfig.__module__ == "pilot.config.mariadb"
    assert config.WafRule.__module__ == "pilot.config.waf"
    assert config.WAF_RULE_ACTIONS == ("block", "log", "skip")
    assert config.WorkerGroup.__module__ == "pilot.config.worker"


def test_config_package_all_lists_public_exports() -> None:
    assert "BenchConfig" in config.__all__
    assert "WafRule" in config.__all__
    assert "WorkerGroup" in config.__all__
