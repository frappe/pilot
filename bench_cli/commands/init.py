from __future__ import annotations

from bench_cli.core.bench import Bench
from bench_cli.managers.process_manager import ProcessManagerFactory
from bench_cli.managers.python_env_manager import PythonEnvManager
from bench_cli.managers.redis_manager import RedisManager


class InitCommand:
    def __init__(self, bench: Bench) -> None:
        self.bench = bench
        self._step_counter = 0
        self._total_steps = 0

    def run(self) -> None:
        production = self.bench.config.production.nginx
        volume_enabled = self.bench.config.volume.enabled
        self._total_steps = 10 + (3 if production else 0) + (1 if volume_enabled else 0)

        self._step("Validate bench.toml")
        self.bench.config.validate()

        self._step("Install system packages")
        self._install_system_packages()

        if volume_enabled:
            self._step("Set up ZFS volumes")
            self._setup_volume()

        self._step("Create bench directory structure")
        self.bench.create_directories()
        self.bench.write_common_site_config()

        self._step("Create Python virtualenv")
        python_env_manager = PythonEnvManager(self.bench)
        python_env_manager.ensure_python()
        python_env_manager.create_venv()

        self._step("Clone and install framework app")
        for app in self.bench.init_apps():
            if not app.is_cloned:
                print(f"  Cloning {app.config.name}...")
                app.clone()
            print(f"  Installing {app.config.name}...")
            python_env_manager.install_app(app)
        self.bench.write_apps_txt()

        self._step("Install Node.js")
        python_env_manager.install_node()

        self._step("Install Node.js dependencies")
        python_env_manager.install_node_dependencies()

        self._step("Configure Redis")
        RedisManager(self.bench.config.redis, self.bench).generate_configs()

        self._step("Download admin frontend")
        self._download_admin_frontend()

        self._step("Generate process config")
        self._write_common_config_for_production(production)
        ProcessManagerFactory.create(self.bench).generate_config()

        if production:
            self._step("Setup process manager")
            self._setup_process_manager()
            self._step("Setup nginx")
            self._setup_nginx()
            self._step("Setup Let's Encrypt SSL")
            self._setup_letsencrypt()

        print("\nBench initialised. Next steps:")
        print("  bench new-site site1.example.com   # create your first site")
        print("  bench start                        # start all processes")

    def _step(self, description: str) -> None:
        self._step_counter += 1
        print(f"[{self._step_counter}/{self._total_steps}] {description}...", flush=True)

    def _download_admin_frontend(self) -> None:
        from bench_cli.commands.admin import download_admin_frontend, BuildAdminCommand, _cli_root
        if not download_admin_frontend(_cli_root()):
            print("  Pre-built download failed — building from source (requires Node.js)...")
            BuildAdminCommand().run()

    def _setup_volume(self) -> None:
        from bench_cli.commands.volume import VolumeSetupCommand

        VolumeSetupCommand(self.bench.config.volume, self.bench.path).run()

    def _install_system_packages(self) -> None:
        from bench_cli.managers.mariadb_manager import MariaDBManager
        from bench_cli.platform import get_package_manager, is_linux

        pkg = get_package_manager()
        if is_linux():
            pkg.update()

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

    def _setup_process_manager(self) -> None:
        if self.bench.config.production.lightweight:
            from bench_cli.managers.systemd_process_manager import SystemdProcessManager
            mgr = SystemdProcessManager(self.bench)
        else:
            from bench_cli.platform import get_package_manager, is_linux
            pkg = get_package_manager()
            if is_linux() and not pkg.is_installed("supervisor"):
                pkg.install("supervisor")
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
