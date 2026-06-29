from pathlib import Path

import pytest

from pilot.config.app_config import AppConfig
from pilot.config.bench_config import BenchConfig
from pilot.config.mariadb_config import MariaDBConfig
from pilot.config.redis_config import RedisConfig
from pilot.config.site_config import SiteConfig
from pilot.config.worker_config import WorkerConfig, WorkerGroup
from pilot.core.app import App
from pilot.core.bench import Bench
from pilot.core.site import Site
from pilot.managers.process_manager import ProcessManager


FIXTURES_DIR = Path(__file__).parent / "fixtures"


def make_bench(tmp_path: Path) -> Bench:
    config = BenchConfig(
        name="test-bench",
        python_version="3.14",
        apps=[
            AppConfig(name="frappe", repo="https://github.com/frappe/frappe", branch="version-16"),
        ],
        mariadb=MariaDBConfig(root_password="root"),
        redis=RedisConfig(cache_port=13000, queue_port=11000),
        workers=WorkerConfig(groups=[
            WorkerGroup(queues=["default"], count=2),
            WorkerGroup(queues=["short"], count=1),
            WorkerGroup(queues=["long"], count=1),
        ]),
    )
    return Bench(config, tmp_path)


# ── App tests ────────────────────────────────────────────────────────────────


def test_app_is_cloned_returns_false_for_nonexistent_path(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="frappe", repo="https://example.com/frappe", branch="main")
    app = App(app_config, bench)
    assert app.is_cloned is False


def test_app_is_cloned_returns_false_when_no_git_directory(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="myapp", repo="https://example.com/myapp", branch="main")
    app = App(app_config, bench)
    app.path.mkdir(parents=True)
    assert app.is_cloned is False


def test_app_is_cloned_returns_true_when_git_directory_exists(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="myapp", repo="https://example.com/myapp", branch="main")
    app = App(app_config, bench)
    app.path.mkdir(parents=True)
    (app.path / ".git").mkdir()
    assert app.is_cloned is True


def test_app_path_is_under_apps_directory(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    app_config = AppConfig(name="frappe", repo="https://example.com", branch="main")
    app = App(app_config, bench)
    assert app.path == tmp_path / "apps" / "frappe"


# ── Site tests ───────────────────────────────────────────────────────────────


def test_site_exists_returns_false_for_nonexistent_path(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    assert site.exists is False


def test_site_exists_returns_false_when_no_site_config_json(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    site.path.mkdir(parents=True)
    assert site.exists is False


def test_site_exists_returns_true_when_site_config_json_present(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    site.path.mkdir(parents=True)
    (site.path / "site_config.json").write_text("{}")
    assert site.exists is True


def test_site_path_is_under_sites_directory(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    site_config = SiteConfig(name="site1.localhost", apps=["frappe"])
    site = Site(site_config, bench)
    assert site.path == tmp_path / "sites" / "site1.localhost"


# ── Bench tests ───────────────────────────────────────────────────────────────


def test_bench_create_directories(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    assert (tmp_path / "apps").is_dir()
    assert (tmp_path / "sites").is_dir()
    assert (tmp_path / "sites" / "assets").is_dir()
    assert (tmp_path / "logs").is_dir()
    assert (tmp_path / "config").is_dir()
    assert (tmp_path / "pids").is_dir()


def test_bench_apps_scans_filesystem(tmp_path: Path) -> None:
    """bench.apps() discovers apps from apps/ directory, not bench.toml."""
    bench = make_bench(tmp_path)
    bench.create_directories()

    # Create a fake cloned app
    app_dir = tmp_path / "apps" / "testapp"
    app_dir.mkdir()
    (app_dir / ".git").mkdir()

    apps = bench.apps()
    assert len(apps) == 1
    assert apps[0].config.name == "testapp"


def test_bench_apps_ignores_non_git_directories(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    (tmp_path / "apps" / "notapp").mkdir()  # no .git

    apps = bench.apps()
    assert apps == []


def test_bench_sites_scans_filesystem(tmp_path: Path) -> None:
    """bench.sites() discovers sites from sites/ directory."""
    bench = make_bench(tmp_path)
    bench.create_directories()

    site_dir = tmp_path / "sites" / "site1.localhost"
    site_dir.mkdir()
    (site_dir / "site_config.json").write_text("{}")

    sites = bench.sites()
    assert len(sites) == 1
    assert sites[0].config.name == "site1.localhost"


def test_bench_init_apps_comes_from_config(tmp_path: Path) -> None:
    """bench.init_apps() returns apps from bench.toml (used during bench init)."""
    bench = make_bench(tmp_path)
    init_apps = bench.init_apps()
    assert len(init_apps) == 1
    assert init_apps[0].config.name == "frappe"


# ── ProcessManager._process_definitions ──────────────────────────────────────


def test_process_definitions_returns_correct_count(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    # workers: default=2, short=1, long=1 => 4 worker processes
    # plus web, socketio, redis_cache, redis_queue = 4
    # plus admin = 1
    # total = 9
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    assert len(definitions) == 9
    assert "admin-ui" not in [pd.name for pd in definitions]


def test_process_definitions_admin_dev_adds_vite_ui(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    definitions = ProcessManager(bench, admin_dev=True)._process_definitions()
    assert "admin-ui" in [pd.name for pd in definitions]
    assert len(definitions) == 10


def test_process_definitions_worker_names_are_numbered(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    names = [pd.name for pd in definitions]
    assert "worker_default_1" in names
    assert "worker_default_2" in names
    assert "worker_short_1" in names
    assert "worker_long_1" in names


def test_process_definitions_includes_redis_processes(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    names = [pd.name for pd in definitions]
    assert "redis_cache" in names
    assert "redis_queue" in names
    assert "redis_socketio" not in names


def test_process_definitions_order_starts_with_web(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    process_manager = ProcessManager(bench)
    definitions = process_manager._process_definitions()
    assert definitions[0].name == "web"


# ── ProcessManager tests ───────────────────────────────────────────────


def test_honcho_generate_config_writes_procfile(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.generate_config()

    procfile = tmp_path / "config" / "Procfile"
    assert procfile.exists()
    content = procfile.read_text()
    assert "web:" in content
    assert "socketio:" in content
    assert "worker_default_1:" in content
    assert "redis_cache:" in content


def test_honcho_generate_config_writes_redis_configs(tmp_path: Path) -> None:
    # The generated Procfile runs `redis-server config/redis_{cache,queue}.conf`,
    # so generate_config must (re)create those files. Regression: an upgraded
    # bench whose config dir predates the split redis layout used to fail to
    # start with "can't open config file".
    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.generate_config()

    assert (tmp_path / "config" / "redis_cache.conf").exists()
    assert (tmp_path / "config" / "redis_queue.conf").exists()


def test_honcho_generate_config_procfile_format(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.generate_config()

    procfile = tmp_path / "config" / "Procfile"
    content = procfile.read_text()
    for line in content.strip().splitlines():
        assert ": " in line, f"Line missing ': ' separator: {line!r}"


def test_honcho_start_writes_per_process_pid_files(tmp_path: Path) -> None:
    """Each spawned process gets its own pids/<name>.pid file."""
    import subprocess
    from unittest.mock import MagicMock, patch

    bench = make_bench(tmp_path)
    bench.create_directories()
    process_manager = ProcessManager(bench)
    process_manager.generate_config()

    fake_proc = MagicMock()
    fake_proc.pid = 12345
    fake_proc.stdout = iter([])
    fake_proc.poll.return_value = None
    fake_proc.wait.return_value = 0

    def fake_popen(cmd, **kwargs):
        return fake_proc

    with patch("pilot.managers.process_manager.subprocess.Popen", side_effect=fake_popen):
        with patch.object(process_manager, "_stop_all"):
            for pd in process_manager._process_definitions():
                proc = fake_popen(pd.command)
                process_manager._procs[pd.name] = proc
                (bench.pids_path / f"{pd.name}.pid").write_text(str(proc.pid))

    for name in process_manager._procs:
        pid_file = bench.pids_path / f"{name}.pid"
        assert pid_file.exists(), f"Missing PID file for process '{name}'"
        assert pid_file.read_text().strip() == "12345"


# ── Site.create() database engine selection ───────────────────────────────────


# ── Site create/restore/reinstall use the bench's single engine ───────────────


def _capture_site_cmd(monkeypatch) -> dict:
    captured: dict = {}
    monkeypatch.setattr("pilot.core.site.run_command", lambda cmd, **kw: captured.setdefault("cmd", cmd))
    return captured


def _postgres_bench(tmp_path: Path, **postgres):
    bench = make_bench(tmp_path)
    bench.config.db_type = "postgres"
    for key, value in postgres.items():
        setattr(bench.config.postgres, key, value)
    return bench


def test_site_create_postgres_builds_db_args(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgsecret", port=5433)
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=["frappe"]), bench).create()

    cmd = captured["cmd"]
    assert cmd[cmd.index("--db-type") + 1] == "postgres"
    assert cmd[cmd.index("--db-host") + 1] == "localhost"
    assert cmd[cmd.index("--db-port") + 1] == "5433"
    assert cmd[cmd.index("--db-root-username") + 1] == "postgres"
    assert cmd[cmd.index("--db-root-password") + 1] == "pgsecret"
    assert "--db-socket" not in cmd


def test_site_create_mariadb_when_bench_is_mariadb(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = make_bench(tmp_path)  # bench db_type defaults to mariadb
    captured = _capture_site_cmd(monkeypatch)
    monkeypatch.setattr("pilot.managers.mariadb_manager.MariaDBManager._detect_socket", lambda self: "")

    Site(SiteConfig(name="mdb.localhost", apps=["frappe"]), bench).create()

    cmd = captured["cmd"]
    # mariadb is frappe's default engine — no --db-type flag is passed
    assert "--db-type" not in cmd
    assert cmd[cmd.index("--db-root-username") + 1] == "root"
    assert "--db-host" in cmd


def test_site_restore_uses_postgres_root_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgpw")
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=[]), bench).restore("/tmp/db.sql.gz")

    cmd = captured["cmd"]
    assert "restore" in cmd
    assert "--db-type" not in cmd  # restore reads the engine from the site's config
    assert cmd[cmd.index("--db-root-username") + 1] == "postgres"
    assert cmd[cmd.index("--db-root-password") + 1] == "pgpw"


def test_site_restore_uses_mariadb_root_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = make_bench(tmp_path)  # mariadb bench, root_password="root"
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).restore(
        "/tmp/db.sql.gz", public_files="/tmp/pub.tar", private_files="/tmp/priv.tar"
    )

    cmd = captured["cmd"]
    assert "--db-type" not in cmd
    assert cmd[cmd.index("--db-root-username") + 1] == "root"
    assert cmd[cmd.index("--with-public-files") + 1] == "/tmp/pub.tar"
    assert cmd[cmd.index("--with-private-files") + 1] == "/tmp/priv.tar"


def test_site_reinstall_postgres_root_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgpw")
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=[]), bench).reinstall("secret")

    cmd = captured["cmd"]
    assert "reinstall" in cmd and "--yes" in cmd
    assert cmd[cmd.index("--admin-password") + 1] == "secret"
    assert cmd[cmd.index("--db-root-username") + 1] == "postgres"
    assert cmd[cmd.index("--db-root-password") + 1] == "pgpw"


def test_site_reinstall_mariadb_root_creds(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = make_bench(tmp_path)
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="m.localhost", apps=[]), bench).reinstall("secret")

    cmd = captured["cmd"]
    assert cmd[cmd.index("--db-root-username") + 1] == "root"


def test_site_create_postgres_empty_password_uses_placeholder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    bench = _postgres_bench(tmp_path, root_password="")  # trust/peer auth — no password
    captured = _capture_site_cmd(monkeypatch)

    Site(SiteConfig(name="pg.localhost", apps=[]), bench).create()

    cmd = captured["cmd"]
    # frappe prompts on an empty password (hanging the task); a placeholder avoids it.
    assert cmd[cmd.index("--db-root-password") + 1] == "trust_auth"


def test_bench_db_root_args_postgres(tmp_path: Path) -> None:
    bench = _postgres_bench(tmp_path, root_password="pgpw")
    assert bench.db_root_args() == ["--db-root-username", "postgres", "--db-root-password", "pgpw"]


def test_bench_db_root_args_mariadb(tmp_path: Path) -> None:
    bench = make_bench(tmp_path)
    assert bench.db_root_args() == ["--db-root-username", "root", "--db-root-password", "root"]
