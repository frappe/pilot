from __future__ import annotations

from bench_cli.core.bench import Bench
from bench_cli.managers.python_env_manager import PythonEnvManager
from bench_cli.managers.redis_manager import RedisManager
from bench_cli.managers.process_manager import ProcessManagerFactory


class InitCommand:
    def __init__(self, bench: Bench) -> None:
        self.bench = bench

    def run(self) -> None:
        production = self.bench.config.nginx.enabled
        total = 12 if production else 9

        self._step(1, total, "Validate bench.toml")
        self.bench.config.validate()

        self._step(2, total, "Install system packages")
        self._install_system_packages()

        self._step(3, total, "Create bench directory structure")
        self.bench.create_directories()
        self.bench.write_common_site_config()

        self._step(4, total, "Create Python virtualenv")
        python_env_manager = PythonEnvManager(self.bench)
        python_env_manager.ensure_python()
        python_env_manager.create_venv()

        self._step(5, total, "Clone and install framework app")
        for app in self.bench.init_apps():
            if not app.is_cloned:
                print(f"  Cloning {app.config.name}...")
                app.clone()
            print(f"  Installing {app.config.name}...")
            python_env_manager.install_app(app)
        self.bench.write_apps_txt()

        self._step(6, total, "Install Node.js")
        python_env_manager.install_node()

        self._step(7, total, "Install Node.js dependencies")
        python_env_manager.install_node_dependencies()

        self._step(8, total, "Configure Redis")
        RedisManager(self.bench.config.redis, self.bench).generate_configs()

        self._step(9, total, "Generate process config")
        self._write_common_config_for_production(production)
        ProcessManagerFactory.create(self.bench).generate_config()

        if production:
            self._step(10, total, "Setup supervisor")
            self._setup_supervisor()
            self._step(11, total, "Setup nginx")
            self._setup_nginx()
            self._step(12, total, "Setup Let's Encrypt SSL")
            self._setup_letsencrypt()

        print("\nBench initialised. Next steps:")
        print("  bench new-site site1.example.com   # create your first site")
        print("  bench start                        # start all processes")

    def _step(self, number: int, total: int, description: str) -> None:
        print(f"[{number}/{total}] {description}...", flush=True)

    def _install_system_packages(self) -> None:
        from bench_cli.managers.mariadb_manager import MariaDBManager
        from bench_cli.platform import get_package_manager, is_linux
        mariadb_manager = MariaDBManager(self.bench.config.mariadb)
        mariadb_manager.install()
        mariadb_manager.start()
        RedisManager(self.bench.config.redis, self.bench).install()
        if is_linux():
            pkg = get_package_manager()
            pkg.install("build-essential", "pkg-config", "libmariadb-dev", "git")
        PythonEnvManager(self.bench).ensure_python()

    def _write_common_config_for_production(self, production: bool) -> None:
        if not production:
            return
        import json
        common_config_path = self.bench.sites_path / "common_site_config.json"
        existing: dict = {}
        if common_config_path.exists():
            try:
                existing = json.loads(common_config_path.read_text())
            except Exception:
                pass
        existing["dns_multitenant"] = 1
        common_config_path.write_text(json.dumps(existing, indent=2))

    def _setup_supervisor(self) -> None:
        from bench_cli.platform import get_package_manager
        get_package_manager().install("supervisor")
        from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager
        mgr = SupervisorProcessManager(self.bench)
        mgr.install_config()
        mgr.reload()

    def _setup_nginx(self) -> None:
        from bench_cli.commands.setup.nginx import SetupNginxCommand
        SetupNginxCommand(self.bench).run()

    def _setup_letsencrypt(self) -> None:
        if not self.bench.config.letsencrypt.email:
            print("  Skipped — no letsencrypt.email set in bench.toml")
            return
        from bench_cli.commands.setup.letsencrypt import SetupLetsEncryptCommand
        SetupLetsEncryptCommand(self.bench).run()
