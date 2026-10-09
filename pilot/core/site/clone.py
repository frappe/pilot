from __future__ import annotations

import json
import secrets
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pilot.core.bench.artifacts import BenchArtifacts
from pilot.core.site.config import query_installed_apps_via_db
from pilot.core.site.provisioning import (
    SiteProvisioner,
    register_with_provider,
    should_enable_ssl,
    validate_new_site,
)
from pilot.exceptions import BenchError
from pilot.internal.atomic_file import exclusive_file_lock
from pilot.internal.validators import validate_site_name
from pilot.utils import hosts_line_contains, write_private_text


class SiteClone:
    """Fresh independent database, uploads and credentials in a selected bench."""

    def __init__(self, source, destination, name: str, admin_password: str) -> None:
        self.source = source
        self.bench = destination
        self.name = name
        self.password = admin_password
        self.route_registered = False
        self.hosts_added = False
        self.nginx_changed = False

    def validate(self) -> bool:
        error = validate_site_name(self.name) or validate_site_name(self.source.config.name)
        if error:
            raise BenchError(error)
        if not self.source.exists:
            raise BenchError("Source site does not exist.")
        if self.source.bench.config.db_type != self.bench.config.db_type:
            raise BenchError("Source and destination must use the same database engine.")
        if self.bench.config.db_type not in ("mariadb", "postgres"):
            raise BenchError("Site cloning currently supports MariaDB and PostgreSQL.")
        if not self.password.strip():
            raise BenchError("Administrator password must not be empty.")
        if (self.bench.sites_path / self.name).exists():
            raise BenchError("Destination site directory already exists.")
        apps = query_installed_apps_via_db(self.source.bench.path, self.source.config.name)
        if apps is None:
            raise BenchError("Could not read the source site's installed apps.")
        return validate_new_site(self.bench, self.name, apps)

    def run(self, on_progress=print, *, prepare_bench=None):
        from pilot.core.site.clone_database import SiteDatabaseClone

        with exclusive_file_lock(self.bench.path.parent / f"clone-site-{self.name}"):
            self.route_registered = False
            self.hosts_added = False
            self.nginx_changed = False
            wildcard = self.validate()
            site = self.bench.site(self.name)
            config = self.site_config()
            database = SiteDatabaseClone(self.source, site, config)
            site.path.mkdir(mode=0o700)
            try:
                return self.populate(site, database, wildcard, on_progress, prepare_bench)
            except BaseException as error:
                self.rollback(site, database, error, on_progress)
                raise

    def populate(self, site, database, wildcard: bool, on_progress, prepare_bench=None):
        from pilot.core.bench.cloning.runtime import ForkRuntime
        from pilot.core.site.login import site_url

        site.config.route = register_with_provider(self.bench, self.name) if wildcard else None
        self.route_registered = wildcard
        site.config.ssl = (
            site.config.route.public_tls if site.config.route else should_enable_ssl(self.bench, self.name)
        )
        config = database.config
        routing = {"ssl": site.config.ssl}
        if site.config.route:
            routing["route"] = site.config.route.to_dict()
        config["host_name"] = site_url(self.name, routing, self.bench.config)
        write_private_text(site.path / "site_config.json", json.dumps(config, indent=2))
        self.prepare(site, database, on_progress, prepare_bench)
        with ForkRuntime(self.bench, allow_existing=True):
            self.finish(site)
        on_progress(f"Site '{self.name}' cloned with scheduler and outgoing mail disabled.")
        return site

    def prepare(self, site, database, on_progress, prepare_bench=None) -> None:
        on_progress("Streaming source database into a new database")
        with ThreadPoolExecutor(max_workers=2) as executor:
            imported = executor.submit(database.run)
            on_progress("Copying public and private uploads")
            files = executor.submit(self.copy_files, site)
            error: BaseException | None = None
            try:
                if prepare_bench is not None:
                    prepare_bench()
            except BaseException as failure:
                error = failure
            for stage, job in (("Database import", imported), ("Upload copying", files)):
                try:
                    job.result()
                except BaseException as failure:
                    if error is None:
                        error = failure
                    else:
                        message = f"{stage} failed: {failure}"
                        error.add_note(message)
                        on_progress(message)
            if error is not None:
                raise error

    def rollback(self, site, database, error: BaseException, on_progress) -> None:
        from pilot.core.site.drop import SiteDropper

        if self.route_registered:
            SiteDropper(site).release_domains([self.name])
        try:
            if self.hosts_added:
                from pilot.managers.platform import remove_hosts_entry

                remove_hosts_entry(self.name)
            if (site.path / "site_config.json").exists():
                database.cleanup()
            shutil.rmtree(site.path)
            if self.nginx_changed:
                from pilot.managers.nginx import NginxManager

                NginxManager(self.bench).reload_for_site_change()
        except Exception as cleanup_error:
            error.add_note(f"Clone cleanup failed: {cleanup_error}")
            on_progress(f"Clone cleanup failed; retained recovery files at {site.path}.")

    def site_config(self) -> dict:
        original = json.loads((self.source.path / "site_config.json").read_text())
        database = f"_{secrets.token_hex(8)}"
        engine = self.bench.config.db_type
        settings = getattr(self.bench.config, engine)
        config = {
            "db_type": engine,
            "db_name": database,
            "db_user": database,
            "db_password": secrets.token_urlsafe(24),
            "db_host": settings.host,
            "db_port": settings.port,
            "pause_scheduler": 1,
            "mute_emails": 1,
            "maintenance_mode": 1,
            "host_name": f"http://{self.name}:{self.bench.config.http_port}",
        }
        if original.get("encryption_key"):
            config["encryption_key"] = original["encryption_key"]
        if engine == "mariadb" and settings.socket_path:
            config["db_socket"] = settings.socket_path
        return config

    def copy_files(self, site) -> None:
        for relative in ("public/files", "private/files", "private/backups", "logs", "locks"):
            (site.path / relative).mkdir(parents=True, exist_ok=True)
        for relative in ("public/files", "private/files"):
            source = self.source.path / relative
            if source.is_dir():
                BenchArtifacts.copy_directory(source, site.path / relative)
        BenchArtifacts.relocate_links(self.source.path.resolve(), site.path.resolve())

    def finish(self, site) -> None:
        from pilot.core.site.commands import SiteCommands

        provisioner = SiteProvisioner(self.bench, self.name, [], self.password)
        SiteCommands(site).set_admin_password(self.password)
        provisioner.write_route_policy(site)
        provisioner.write_pilot_communication_config(site)
        self.bench.write_common_site_config()
        if self.bench.config.production.process_manager == "none":
            self.hosts_added = not any(
                hosts_line_contains(line, self.name) for line in Path("/etc/hosts").read_text().splitlines()
            )
        provisioner.add_to_hosts(site)
        self.nginx_changed = True
        provisioner.reload_nginx()
        origin_tls = site.config.route.origin_tls if site.config.route else site.config.ssl
        if origin_tls:
            provisioner.obtain_cert(site, print)
        site.set_maintenance_settings({"maintenance_mode": 0, "pause_scheduler": 1})
