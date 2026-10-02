from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import run_command, write_private_text

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.site import Site


class SiteTemplate:
    """A reusable backup with the exact source revisions that produced it."""

    def __init__(self, path: Path) -> None:
        self.path = path.resolve()

    def prepare(
        self, site: Site, on_progress=print, *, build_assets=True, reuse_source_artifacts=False
    ) -> None:
        from pilot.core.bench.artifacts import BenchArtifacts
        from pilot.internal.git import GitRepo
        from pilot.managers.environment import PythonEnvManager

        if site.bench.config.production.enabled:
            raise BenchError("Prepare templates from a development fixture site.")
        if site.bench.config.db_type not in ("mariadb", "postgres"):
            raise BenchError("Development templates support MariaDB and PostgreSQL.")
        config = json.loads((site.path / "site_config.json").read_text())
        if config.get("encrypt_backup"):
            raise BenchError("Development templates require unencrypted backups.")
        apps = []
        configured = {app.name: app for app in site.bench.config.apps}
        for app in site.bench.apps():
            repo = GitRepo(app.path)
            if run_command(["git", "-C", str(app.path), "status", "--porcelain"]).stdout.strip():
                raise BenchError(f"Commit or stash changes in {app.config.name} before preparing a template.")
            repository = app.config.repo or configured[app.config.name].repo
            apps.append({"name": app.config.name, "repo": repository, "commit": repo.head_sha})
        missing = set(site.bench.registered_apps()) - {app["name"] for app in apps}
        if missing:
            raise BenchError(f"Template apps must be Git repositories: {', '.join(sorted(missing))}")
        self.path.mkdir(mode=0o700, parents=True, exist_ok=False)
        if build_assets:
            on_progress("Building template assets once")
            PythonEnvManager(site.bench).build_assets()
        artifact_source = str(site.bench.path.resolve()) if reuse_source_artifacts else ""
        artifacts = (
            BenchArtifacts.paths(site.bench)
            if reuse_source_artifacts
            else BenchArtifacts(self.path / "artifacts").capture(site.bench)
        )
        on_progress(f"Backing up fixture {site.config.name} into {self.path}")
        run_command(
            [
                *site.bench.frappe_call,
                "frappe",
                "--site",
                site.config.name,
                "backup",
                "--with-files",
                "--backup-path",
                str(self.path),
            ],
            cwd=site.bench.sites_path,
            stream_output=True,
        )
        files = self.backup_files()
        self.ensure_revisions_unchanged(site.bench, apps)
        write_private_text(
            self.path / "template.json",
            json.dumps(
                {
                    "version": 1,
                    "engine": site.bench.config.db_type,
                    "apps": apps,
                    "files": files,
                    "artifacts": artifacts,
                    "artifact_source": artifact_source,
                },
                indent=2,
            ),
        )

    @staticmethod
    def ensure_revisions_unchanged(bench: Bench, apps: list[dict]) -> None:
        from pilot.internal.git import GitRepo

        for entry in apps:
            path = bench.app(entry["name"]).path
            status = run_command(["git", "-C", str(path), "status", "--porcelain"]).stdout.strip()
            if GitRepo(path).head_sha != entry["commit"] or status:
                raise BenchError("Source changed while preparing the template; prepare a new template.")

    def backup_files(self) -> dict[str, str]:
        suffixes = {
            "database": "-database.sql.gz",
            "public": "-files.tar",
            "private": "-private-files.tar",
            "config": "-site_config_backup.json",
        }
        files = {}
        for kind, suffix in suffixes.items():
            matches = [p for p in self.path.iterdir() if p.name.endswith(suffix)]
            if kind == "public":
                matches = [p for p in matches if not p.name.endswith("-private-files.tar")]
            if len(matches) != 1:
                raise BenchError(f"Template must contain exactly one {kind} backup ({suffix}).")
            files[kind] = matches[0].name
        return files

    def read(self) -> dict:
        data = json.loads((self.path / "template.json").read_text())
        if data.get("version") != 1:
            raise BenchError("Unsupported development template version.")
        for filename in data["files"].values():
            path = (self.path / filename).resolve()
            if path.parent != self.path or not path.is_file():
                raise BenchError(f"Missing or invalid template file: {filename}")
        root = Path(data.get("artifact_source") or self.path / "artifacts").resolve()
        for relative in data.get("artifacts", []):
            path = root / relative
            if not path.resolve().is_relative_to(root) or not path.is_dir():
                raise BenchError(f"Missing or invalid template artifact: {relative}")
        return data

    def restore_into(self, bench: Bench, name: str) -> Site:
        import secrets

        from pilot.core.site.commands import SiteCommands
        from pilot.core.site.provisioning import SiteProvisioner

        data = self.read()
        if data["engine"] != bench.config.db_type:
            raise BenchError("Template and destination must use the same database engine.")
        site = bench.site(name)
        site.path.mkdir(mode=0o700)
        source_config = json.loads((self.path / data["files"]["config"]).read_text())
        database = f"_{secrets.token_hex(8)}"
        config = {
            "db_name": database,
            "db_user": database,
            "db_password": secrets.token_urlsafe(24),
            "db_type": data["engine"],
            "developer_mode": 1,
            "pause_scheduler": 1,
            "host_name": f"http://{name}:{bench.config.http_port}",
        }
        if source_config.get("encryption_key"):
            config["encryption_key"] = source_config["encryption_key"]
        if data["engine"] == "postgres":
            config["db_host"] = bench.config.postgres.host
            config["db_port"] = bench.config.postgres.port
        else:
            from pilot.managers.database import MariaDBManager

            config["db_host"] = bench.config.mariadb.host
            config["db_port"] = bench.config.mariadb.port
            if socket_path := MariaDBManager(bench.config.mariadb)._detect_socket():
                config["db_socket"] = socket_path
        write_private_text(site.path / "site_config.json", json.dumps(config, indent=2))
        commands = SiteCommands(site)
        argv = [
            *bench.frappe_call,
            "frappe",
            "--site",
            name,
            "restore",
            str(self.path / data["files"]["database"]),
            "--admin-password",
            "admin",
            "--with-public-files",
            str(self.path / data["files"]["public"]),
            "--with-private-files",
            str(self.path / data["files"]["private"]),
        ]
        with commands.setup_credentials(data["engine"], database) as credentials:
            run_command(argv + credentials, cwd=bench.sites_path, stream_output=True)
        run_command(
            [*bench.frappe_call, "frappe", "--site", name, "set-admin-password", "admin"],
            cwd=bench.sites_path,
            stream_output=True,
        )
        site.set_maintenance_settings({"maintenance_mode": 0, "pause_scheduler": 1})
        SiteProvisioner(bench, name, [], "admin").write_pilot_communication_config(site)
        return site
