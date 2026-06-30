"""Site.restore passes the bench's MariaDB connection so a MariaDB backup on a
PostgreSQL bench can be staged for pgloader conversion."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.config.app_config import AppConfig
from pilot.config.bench_config import BenchConfig
from pilot.config.mariadb_config import MariaDBConfig
from pilot.config.postgres_config import PostgresConfig
from pilot.config.redis_config import RedisConfig
from pilot.config.site_config import SiteConfig
from pilot.config.worker_config import WorkerConfig, WorkerGroup
from pilot.core.bench import Bench
from pilot.core.site import Site


def _restore_cmd(tmp_path: Path, db_type: str) -> list[str]:
    bench_dir = tmp_path / "benches" / "b1"
    bench_dir.mkdir(parents=True, exist_ok=True)
    config = BenchConfig(
        name="b1",
        python_version="3.14",
        apps=[AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16")],
        mariadb=MariaDBConfig(host="mariahost", port=3308, admin_user="root", root_password="mpw"),
        postgres=PostgresConfig(admin_user="postgres", root_password="ppw"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[WorkerGroup(queues=["default"], count=1)]),
        db_type=db_type,
    )
    site = Site(SiteConfig(name="s.localhost", apps=[]), Bench(config, bench_dir))
    with patch("pilot.core.site.run_command") as rc:
        site.restore("/tmp/backup.sql.gz")
    return rc.call_args.args[0]


def test_restore_passes_source_mariadb_on_postgres_bench(tmp_path: Path) -> None:
    cmd = _restore_cmd(tmp_path, "postgres")
    assert "--source-mariadb-host" in cmd
    assert cmd[cmd.index("--source-mariadb-host") + 1] == "mariahost"
    assert cmd[cmd.index("--source-mariadb-port") + 1] == "3308"
    assert cmd[cmd.index("--source-mariadb-root-username") + 1] == "root"


def test_restore_omits_source_mariadb_on_mariadb_bench(tmp_path: Path) -> None:
    cmd = _restore_cmd(tmp_path, "mariadb")
    assert "--source-mariadb-host" not in cmd
