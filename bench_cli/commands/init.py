from __future__ import annotations

import shutil
from collections.abc import Callable

from bench_cli.core.bench import Bench
from bench_cli.managers.process_manager import ProcessManagerFactory
from bench_cli.managers.python_env_manager import PythonEnvManager
from bench_cli.managers.redis_manager import RedisManager

_BENCH_DIRS = ("apps", "sites", "logs", "config", "pids", "env", "admin", "tasks")


class InitCommand:
    def __init__(self, bench: Bench, sudo_password: str = "") -> None:
        self.bench = bench
        self._sudo_password = sudo_password
        self._step_counter = 0
        self._total_steps = 0
        self._rollback_actions: list[tuple[str, Callable[[], None]]] = []

    def run(self) -> None:
        try:
            self._do_run()
        except Exception as exc:
            print(f"\nError: {exc}", flush=True)
            self._rollback()
            raise

    # ── rollback infrastructure ────────────────────────────────────────────

    def _on_rollback(self, label: str, fn: Callable[[], None]) -> None:
        self._rollback_actions.append((label, fn))

    def _rollback(self) -> None:
        if not self._rollback_actions:
            return
        print("\nRolling back changes...", flush=True)
        for label, fn in reversed(self._rollback_actions):
            print(f"  Removing {label}...", flush=True)
            try:
                fn()
            except Exception as e:
                print(f"    Warning: rollback step failed — {e}", flush=True)
        print(
            "\nRollback complete. bench.toml is preserved — fix the issue and run init again.",
            flush=True,
        )

    def _remove_bench_dirs(self) -> None:
        for name in _BENCH_DIRS:
            p = self.bench.path / name
            if p.exists() or p.is_symlink():
                shutil.rmtree(p, ignore_errors=True)

    def _remove_sudoers(self) -> None:
        import getpass
        import subprocess

        path = f"/etc/sudoers.d/{getpass.getuser()}"
        subprocess.run(["sudo", "rm", "-f", path], capture_output=True, check=False)

    def _remove_nginx_symlink(self) -> None:
        import subprocess

        symlink = self.bench.config.nginx.config_dir / f"{self.bench.config.name}.conf"
        if symlink.exists() or symlink.is_symlink():
            subprocess.run(["sudo", "unlink", str(symlink)], capture_output=True, check=False)

    def _remove_systemd_units(self) -> None:
        import subprocess

        from bench_cli.managers.systemd_process_manager import SystemdProcessManager

        mgr = SystemdProcessManager(self.bench)
        for f in mgr.user_unit_dir.glob(f"{self.bench.config.name}*"):
            f.unlink(missing_ok=True)
        subprocess.run(
            ["systemctl", "--user", "daemon-reload"],
            capture_output=True,
            check=False,
            env=mgr._systemctl_env(),
        )

    # ── init steps ─────────────────────────────────────────────────────────

    def _do_run(self) -> None:
        from bench_cli.platform import is_linux

        production = self.bench.config.production.nginx
        volume_enabled = is_linux()  # ZFS is mandatory on Linux; macOS is dev-only
        has_sudoers = bool(self._sudo_password)
        self._total_steps = 10 + (3 if production else 0) + (1 if volume_enabled else 0) + (1 if has_sudoers else 0)

        if has_sudoers:
            self._step("Configure passwordless sudo")
            self._setup_sudoers()

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
        self._on_rollback("bench directories", self._remove_bench_dirs)

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

    def _setup_sudoers(self) -> None:
        import getpass
        import subprocess
        from pathlib import Path

        username = getpass.getuser()
        rules = "\n".join(
            [
                f"{username} ALL=(ALL) NOPASSWD: /usr/bin/apt-get",
                f"{username} ALL=(ALL) NOPASSWD: /usr/sbin/nginx",
                f"{username} ALL=(ALL) NOPASSWD: /usr/bin/systemctl",
                f"{username} ALL=(ALL) NOPASSWD: /usr/bin/loginctl",
                f"{username} ALL=(ALL) NOPASSWD: /usr/bin/ln",
                f"{username} ALL=(ALL) NOPASSWD: /usr/bin/unlink",
                f"{username} ALL=(ALL) NOPASSWD: /usr/sbin/zpool",
                f"{username} ALL=(ALL) NOPASSWD: /usr/sbin/zfs",
                f"{username} ALL=(ALL) NOPASSWD: /usr/bin/rsync",
            ]
        )
        content = f"# Frappe bench — managed by bench init, do not edit\n{rules}\n"
        sudoers_path = f"/etc/sudoers.d/{username}"

        try:
            existing = Path(sudoers_path).read_text()
            if all(rule in existing for rule in rules.splitlines()):
                print(f"  {sudoers_path} already up to date, skipping.")
                return
        except OSError:
            pass

        result = subprocess.run(
            ["sudo", "-S", "tee", sudoers_path],
            input=f"{self._sudo_password}\n{content}",
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode != 0:
            print(f"  Warning: could not write sudoers file — {result.stderr.strip() or 'permission denied'}")
            print("  Sudo operations may prompt for a password during setup.")
            return
        subprocess.run(["sudo", "chmod", "0440", sudoers_path], capture_output=True, check=False)
        print(f"  Wrote {sudoers_path}")
        self._on_rollback(sudoers_path, self._remove_sudoers)

    def _download_admin_frontend(self) -> None:
        from bench_cli.commands.admin import BuildAdminCommand, _cli_root, download_admin_frontend

        if not download_admin_frontend(_cli_root()):
            print("  Pre-built download failed — building from source (requires Node.js)...")
            BuildAdminCommand().run()

    def _setup_volume(self) -> None:
        from bench_cli.commands.volume import VolumeSetupCommand

        VolumeSetupCommand(self.bench.config.volume, self.bench.path, bench_config=self.bench.config).run()

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
        if self.bench.config.production.process_manager == "systemd":
            from bench_cli.managers.systemd_process_manager import SystemdProcessManager

            mgr = SystemdProcessManager(self.bench)
            mgr.install_config()
            mgr.reload()
            self._on_rollback("systemd user units", self._remove_systemd_units)
        else:
            import subprocess

            from bench_cli.platform import get_package_manager, is_linux

            pkg = get_package_manager()
            if is_linux() and not pkg.is_installed("supervisor"):
                pkg.install("supervisor")
                subprocess.run(["sudo", "systemctl", "disable", "--now", "supervisor"], check=False)
            from bench_cli.managers.supervisor_process_manager import SupervisorProcessManager

            mgr = SupervisorProcessManager(self.bench)
            mgr.install_config()
            mgr.reload()
            # supervisor config lives inside config/ — _remove_bench_dirs handles it

    def _setup_nginx(self) -> None:
        from bench_cli.commands.setup.nginx import SetupNginxCommand

        SetupNginxCommand(self.bench).run()
        self._on_rollback("nginx config symlink", self._remove_nginx_symlink)

    def _setup_letsencrypt(self) -> None:
        if not self.bench.config.letsencrypt.email:
            print("  Skipped — no letsencrypt.email set in bench.toml")
            return
        from bench_cli.commands.setup.letsencrypt import SetupLetsEncryptCommand

        SetupLetsEncryptCommand(self.bench).run()
