from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pilot.tasks.jobs.setup_letsencrypt_task import SetupLetsEncryptTask
from pilot.config.toml_store import BenchTomlStore
from pilot.exceptions import BenchError
from tests.test_commands import make_bench


def _task(tmp_path: Path, *, production: bool, email: str = "") -> SetupLetsEncryptTask:
    bench = make_bench(tmp_path)
    bench.config.production.enabled = production
    if production:
        bench.config.production.process_manager = "systemd"
        bench.config.admin.domain = "admin.example.com"
    bench.create_directories()
    BenchTomlStore.for_bench(tmp_path).write(bench.config)
    site_path = tmp_path / "sites" / "secure.localhost"
    site_path.mkdir(parents=True)
    (site_path / "site_config.json").write_text(json.dumps({"ssl": False}))
    return SetupLetsEncryptTask(
        bench,
        tmp_path,
        SimpleNamespace(site="secure.localhost", email=email),
    )


def test_production_preflight_runs_before_tls_configuration_changes(tmp_path: Path) -> None:
    task = _task(tmp_path, production=True, email="ops@example.com")
    config_path = tmp_path / "sites" / "secure.localhost" / "site_config.json"
    original_site_config = config_path.read_bytes()
    original_bench_config = (tmp_path / "bench.toml").read_bytes()

    with (
        patch(
            "pilot.tasks.jobs.base_task.has_passwordless_sudo",
            return_value=False,
        ),
        patch("pilot.tasks.jobs.setup_letsencrypt_task.SetupLetsEncryptCommand.run") as run,
        pytest.raises(BenchError, match="non-interactive system privileges"),
    ):
        task.run()

    run.assert_not_called()
    assert config_path.read_bytes() == original_site_config
    assert (tmp_path / "bench.toml").read_bytes() == original_bench_config


def test_tls_task_applies_email_and_site_flag_before_certificate_setup(tmp_path: Path) -> None:
    task = _task(tmp_path, production=False, email="ops@example.com")
    config_path = tmp_path / "sites" / "secure.localhost" / "site_config.json"

    with patch(
        "pilot.tasks.jobs.setup_letsencrypt_task.SetupLetsEncryptCommand.run"
    ) as run:
        task.run()

    run.assert_called_once_with()
    assert json.loads(config_path.read_text())["ssl"] is True
    assert BenchTomlStore.for_bench(tmp_path).read().letsencrypt.email == "ops@example.com"
