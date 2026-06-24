from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.config.site_config import SiteConfig
from bench_cli.utils import run_command

if TYPE_CHECKING:
    from bench_cli.core.app import App
    from bench_cli.core.bench import Bench


class Site:
    def __init__(self, config: SiteConfig, bench: "Bench") -> None:
        self.config = config
        self.bench = bench

    @property
    def path(self) -> Path:
        return self.bench.sites_path / self.config.name

    @property
    def exists(self) -> bool:
        return (self.path / "site_config.json").exists()

    def _frappe_call(self, *args: str) -> list[str]:
        """Build a frappe bench_helper command."""
        return [*self.bench.frappe_call, *args]

    def create(self) -> None:
        from bench_cli.managers.mariadb_manager import MariaDBManager

        mariadb = self.bench.config.mariadb
        socket_path = MariaDBManager(mariadb)._detect_socket()

        cmd = self._frappe_call(
            "frappe",
            "--site",
            self.config.name,
            "new-site",
            self.config.name,
            "--db-root-username",
            mariadb.admin_user,
            "--admin-password",
            self.config.admin_password,
        )
        if socket_path:
            cmd += ["--db-socket", socket_path]
            # unix_socket auth ignores the password; pass a non-empty placeholder
            # so frappe doesn't fall back to an interactive getpass() prompt
            cmd += ["--db-root-password", mariadb.root_password or "socket_auth"]
        else:
            cmd += ["--db-host", mariadb.host, "--db-port", str(mariadb.port)]
            if mariadb.root_password:
                cmd += ["--db-root-password", mariadb.root_password]

        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)

    def restore(self, db_file: str, public_files: str | None = None, private_files: str | None = None) -> None:
        cmd = self._frappe_call("frappe", "--site", self.config.name, "restore", db_file)
        if public_files:
            cmd += ["--with-public-files", public_files]
        if private_files:
            cmd += ["--with-private-files", private_files]

        cmd += ["--db-root-username", self.bench.config.mariadb.admin_user, "--db-root-password", self.bench.config.mariadb.root_password]

        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)

    def install_app(self, app: "App") -> None:
        run_command(
            self._frappe_call("frappe", "--site", self.config.name, "install-app", app.config.name),
            cwd=self.bench.sites_path,
            stream_output=True,
        )
        self.bench.reload_workers(raises=True)

    def uninstall_app(self, app: "App", force: bool = False) -> None:
        cmd = self._frappe_call("frappe", "--site", self.config.name, "uninstall-app", app.config.name, "--yes", "--no-backup")
        if force:
            cmd.append("--force")
        run_command(cmd, cwd=self.bench.sites_path, stream_output=True)
        self.bench.reload_workers(raises=True)

    def list_apps(self) -> list[str]:
        import subprocess

        result = subprocess.run(
            self._frappe_call("frappe", "--site", self.config.name, "list-apps"),
            cwd=str(self.bench.sites_path),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            return []
        return [line.split()[0] for line in result.stdout.splitlines() if line.strip()]

    def migrate(self) -> None:
        run_command(
            self._frappe_call("frappe", "--site", self.config.name, "migrate"),
            cwd=self.bench.sites_path,
            stream_output=True,
        )
