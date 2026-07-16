"""Integration: nginx accepts the ModSecurity config we generate. Runs the real
`nginx -t` against a generated WAF vhost. Skipped unless the ModSecurity module
and CRS are actually installed (so it no-ops in environments without the WAF)."""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from pilot.config.bench_config import BenchConfig
from pilot.config.waf_config import WafConfig
from pilot.core.bench import Bench
from pilot.managers.nginx_manager import NginxManager
from pilot.managers.waf_manager import WafManager

pytestmark = pytest.mark.integration

_HTTP_PORT = 8974
_DATA: dict = {
    "bench": {"name": "test-bench", "python": "3.14"},
    "apps": [{"name": "frappe", "repo": "https://github.com/frappe/frappe", "branch": "version-16"}],
    "mariadb": {"root_password": "root"},
    "redis": {"cache_port": 13000, "queue_port": 11000},
}


@pytest.fixture(autouse=True)
def _require_waf():
    if shutil.which("nginx") is None:
        pytest.skip("nginx not on PATH")
    if not WafManager.is_installed():
        pytest.skip("ModSecurity module or CRS not installed")


def _make_bench(tmp_path: Path) -> Bench:
    config = BenchConfig._from_dict(_DATA)
    config.nginx.http_port = _HTTP_PORT
    config.waf = WafConfig(enabled=True, mode="DetectionOnly")
    bench = Bench(config, tmp_path)
    site = bench.sites_path / "site1.localhost"
    site.mkdir(parents=True, exist_ok=True)
    (site / "site_config.json").write_text("{}")
    return bench


def _wrapper_conf(tmp_path: Path, include_conf: Path, module: str) -> Path:
    conf = tmp_path / "nginx.conf"
    conf.write_text(
        f"load_module {module};\n"
        f"pid {tmp_path}/nginx.pid;\n"
        f"error_log {tmp_path}/error.log;\n"
        "events {}\n"
        "http {\n"
        f"    access_log {tmp_path}/access.log;\n"
        f"    include {include_conf};\n"
        "}\n"
    )
    return conf


def test_generated_waf_config_passes_nginx_t(tmp_path: Path) -> None:
    bench = _make_bench(tmp_path)
    NginxManager(bench).generate_config(ssl_ready=False)

    nginx_dir = bench.config_path / "nginx"
    site_conf = (nginx_dir / "sites" / "site1.localhost.conf").read_text()
    assert "modsecurity on;" in site_conf

    conf = _wrapper_conf(tmp_path, nginx_dir / "include.conf", WafManager.module_path())
    result = subprocess.run(
        ["nginx", "-t", "-p", str(tmp_path), "-c", str(conf)],
        capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr
