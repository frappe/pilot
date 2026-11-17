from __future__ import annotations

import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.config.bench import BenchConfig
from pilot.core.server.legacy_bench.database import DatabaseServer
from pilot.core.server.legacy_bench.journal import MoveJournal
from pilot.core.server.legacy_bench.reader import FRAMEWORK_APP, LegacyApp, LegacyBench
from pilot.core.server.legacy_bench.site_import import SitePlan
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.config import AppConfig
    from pilot.config.common import CommonConfig
    from pilot.core.bench import Bench
    from pilot.core.site import Site

HOST_FILES = ("env", "Procfile", "config", "sites/common_site_config.json", "sites/apps.txt", "sites/assets")


class LegacyImportError(BenchError):
    """The import stopped after the folder moved; the caller keeps the bench or reverts it."""


@dataclass
class DatabaseLogin:
    """A root login for the old bench's server, saved as Pilot's shared server when Pilot has none."""

    server: DatabaseServer
    user: str
    password: str


@dataclass
class SiteImportResult:
    legacy_name: str
    name: str
    keeps_database: bool
    error: str = ""


@dataclass
class ImportResult:
    legacy_path: Path
    bench: "Bench"
    admin_password: str
    sites: list[SiteImportResult] = field(default_factory=list)

    @property
    def has_failures(self) -> bool:
        return any(site.error for site in self.sites)


def get_available_name(
    name: str, is_taken: Callable[[str], bool], with_suffix: Callable[[str, int], str]
) -> str:
    candidate, number = name, 0
    while is_taken(candidate):
        number += 1
        candidate = with_suffix(name, number)
    return candidate


def get_suffixed_site_name(name: str, number: int) -> str:
    """`something.tld` becomes `something-<number>.tld`."""
    label, dot, rest = name.partition(".")
    return f"{label}-{number}{dot}{rest}"


def get_valid_bench_name(name: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]", "-", name)
    return name if BenchConfig.bench_name_pattern.match(name) else f"bench-{name}"


def get_configured_engine(common: "CommonConfig") -> str:
    """The engine Pilot's shared server runs; MariaDB until only PostgreSQL is set up."""
    return "postgres" if common.postgres.root_password and not common.mariadb.root_password else "mariadb"


class LegacyBenchImporter:
    """Moves a bench made by the `bench` CLI into Pilot's benches folder and rebuilds what is
    tied to the old host. Sites on Pilot's database server keep their database; the others
    get a copy on Pilot's server. Every move is journaled, so `revert` puts the folder back."""

    def __init__(self, legacy: LegacyBench, benches_dir: Path, name: str = "") -> None:
        self.legacy = legacy
        self.benches_dir = benches_dir
        self.journal = MoveJournal()
        self.sites: list[Site] = []
        self.entries: set[Path] = set()
        self.name = get_available_name(
            get_valid_bench_name(name or legacy.name),
            lambda candidate: (
                candidate in BenchConfig.reserved_bench_names or (benches_dir / candidate).exists()
            ),
            lambda base, number: f"{base}-{number}",
        )

    @property
    def bench_path(self) -> Path:
        return self.benches_dir / self.name

    @cached_property
    def common(self) -> "CommonConfig":
        from pilot.config.common import CommonConfig

        return CommonConfig.read(self.benches_dir)

    @property
    def engine(self) -> str:
        return get_configured_engine(self.common)

    @property
    def needs_database_login(self) -> bool:
        """Pilot has no MariaDB server yet: the old bench's server becomes it."""
        return self.engine == "mariadb" and not self.common.mariadb.root_password

    @property
    def pilot_server(self) -> DatabaseServer:
        if self.needs_database_login:
            return self.legacy.database_server
        section = self.common.mariadb if self.engine == "mariadb" else self.common.postgres
        return DatabaseServer(section.host, section.port, getattr(section, "socket_path", ""))

    @cached_property
    def site_plans(self) -> list[SitePlan]:
        """Each site with a hostname free on every bench of this host, and how its database comes over."""
        from pilot.core.bench.directory import BenchDirectory

        siblings = BenchDirectory.siblings_of(self.bench_path)

        chosen: set[str] = set()

        def is_taken(name: str) -> bool:
            return name in chosen or bool(siblings.get_host_owner(name))

        plans = []
        for legacy_site in self.legacy.sites:
            name = get_available_name(legacy_site.name, is_taken, get_suffixed_site_name)
            chosen.add(name)
            keeps_database = legacy_site.database_server.is_same_as(self.pilot_server)
            plans.append(SitePlan(legacy_site, name, keeps_database))
        return plans

    def validate_sites(self) -> None:
        """Pilot copies only MariaDB databases, and only into the engine it is set up for."""
        for plan in self.site_plans:
            site = plan.legacy_site
            if site.db_type != self.engine:
                raise BenchError(
                    f"Site {site.name} uses {site.db_type}, but Pilot's database server is {self.engine}."
                )
            if site.db_type == "postgres" and not plan.keeps_database:
                raise BenchError(
                    f"Site {site.name} is on PostgreSQL server {site.database_server.label}, not Pilot's "
                    f"{self.pilot_server.label}. Pilot cannot copy PostgreSQL databases."
                )

    def save_database_login(self, login: DatabaseLogin) -> None:
        """Make the old bench's server Pilot's shared MariaDB server."""
        from pilot.config.common import CommonConfig
        from pilot.core.server.legacy_bench.database import check_login

        check_login(login.server, login.user, login.password)
        with CommonConfig.open(self.benches_dir) as common:
            mariadb = common.mariadb
            mariadb.existing = True
            mariadb.host, mariadb.port, mariadb.socket_path = (
                login.server.host,
                login.server.port,
                login.server.socket,
            )
            mariadb.admin_user, mariadb.root_password = login.user, login.password
        self.__dict__.pop("common", None)

    def run(self, on_progress: Callable[[str], None] = print) -> ImportResult:
        """Everything before the move only reads, so a failure there leaves the old bench as it was."""
        self.validate_sites()
        validate_legacy_apps(self.legacy, on_progress)
        app_configs = [get_app_config(legacy_app) for legacy_app in self.legacy.apps]
        site_plans = self.site_plans
        on_progress(f"Moving {self.legacy.path} to {self.bench_path}...")
        self.journal.move(self.legacy.path, self.bench_path)
        self.entries = {path.relative_to(self.bench_path) for path in self.get_entries()}
        try:
            bench, admin_password = self.rebuild(app_configs, on_progress)
        except Exception as error:
            raise LegacyImportError(str(error) or type(error).__name__) from error
        return ImportResult(
            self.legacy.path, bench, admin_password, self.import_sites(bench, site_plans, on_progress)
        )

    @property
    def sites_path(self) -> Path:
        return self.bench_path / "sites"

    def get_entries(self) -> list[Path]:
        """What sits at the top of the bench and of its sites folder."""
        return [*self.bench_path.iterdir(), *self.sites_path.iterdir()]

    def rebuild(
        self, app_configs: list["AppConfig"], on_progress: Callable[[str], None]
    ) -> tuple["Bench", str]:
        """Set aside the host files, then let Pilot build the bench around the moved apps."""
        legacy = self.bench_path / ".legacy"
        for name in HOST_FILES:
            if (self.bench_path / name).exists():
                self.journal.move(self.bench_path / name, legacy / name)
        for plan in self.site_plans:
            self.journal.move(
                self.sites_path / plan.legacy_site.name, legacy / "sites" / plan.legacy_site.name
            )
        installed = {config.name for config in app_configs}
        for app in sorted((self.bench_path / "apps").iterdir()):
            if app.name != FRAMEWORK_APP:
                self.journal.move(app, legacy / ("pending" if app.name in installed else "apps") / app.name)
        bench, admin_password = self.create_bench(app_configs[0], on_progress)
        bench.initialize(on_progress=on_progress)
        for config in app_configs[1:]:
            self.journal.move(legacy / "pending" / config.name, bench.apps_path / config.name)
            self.install_app(bench, config, on_progress)
        return bench, admin_password

    def create_bench(self, framework: "AppConfig", on_progress: Callable[[str], None]) -> tuple["Bench", str]:
        import secrets

        from pilot.core.bench import Bench

        rename_upstream_remote(self.bench_path / "apps" / FRAMEWORK_APP)
        admin_password = secrets.token_urlsafe(12)
        bench = Bench.create_at(
            self.bench_path,
            self.name,
            db_type=self.engine,
            admin_password=admin_password,
            frappe_branch=framework.branch,
            configure=lambda config: set_framework_repo(config, framework.repo),
        )
        on_progress(f"Created bench '{self.name}' at {bench.path}")
        return bench, admin_password

    def install_app(self, bench: "Bench", config: "AppConfig", on_progress: Callable[[str], None]) -> None:
        from pilot.core.app import App

        rename_upstream_remote(bench.apps_path / config.name)
        App(config, bench).install(on_progress=on_progress)

    def import_sites(
        self, bench: "Bench", plans: list[SitePlan], on_progress: Callable[[str], None]
    ) -> list[SiteImportResult]:
        """Migrate needs Redis, and the new bench is not started yet. A failing site is reported and skipped."""
        from pilot.core.server.legacy_bench.site_import import LegacySiteImport
        from pilot.managers.redis import RedisManager

        results = []
        with RedisManager(bench.config.redis, bench).running():
            for plan in plans:
                on_progress(f"\nImporting site {plan.legacy_site.name} as {plan.name}...")
                result = SiteImportResult(plan.legacy_site.name, plan.name, plan.keeps_database)
                try:
                    self.sites.append(LegacySiteImport(plan, bench, self.journal).run(on_progress))
                except Exception as error:
                    on_progress(f"Importing {plan.legacy_site.name} failed: {error}")
                    result.error = str(error) or type(error).__name__
                results.append(result)
        return results

    def revert(self, on_progress: Callable[[str], None] = print) -> None:
        """Put the folder back as it was. Copied databases are dropped; kept ones stay, migrated."""
        for site in self.sites:
            if site.config.name in {plan.name for plan in self.site_plans if not plan.keeps_database}:
                site.drop(on_progress, no_backup=True, force=True)
            else:
                forget_site(site)
        on_progress("Putting the old bench back...")
        self.journal.undo(keep=1, on_progress=on_progress)
        for path in self.get_entries():
            if path.relative_to(self.bench_path) not in self.entries:
                shutil.rmtree(path) if path.is_dir() and not path.is_symlink() else path.unlink()
        self.journal.undo(on_progress=on_progress)
        on_progress(f"Moved the bench back to {self.legacy.path}.")


def forget_site(site: "Site") -> None:
    """Undo what Pilot registered for an adopted site, leaving its database alone."""
    from pilot.managers.platform import HostsFile

    site.backups.clear_schedule()
    HostsFile().remove(site.config.name)


def rename_upstream_remote(path: Path) -> None:
    """Pilot reads the `origin` remote, which the `bench` CLI names `upstream`."""
    from pilot.internal.command import run_command

    remotes = run_command(["git", "-C", str(path), "remote"]).stdout.decode().split()
    if "origin" not in remotes and "upstream" in remotes:
        run_command(["git", "-C", str(path), "remote", "rename", "upstream", "origin"])


def set_framework_repo(config: "BenchConfig", repo: str) -> None:
    if repo:
        config.framework_app.repo = repo


def get_app_config(legacy_app: LegacyApp) -> "AppConfig":
    """Read before the move, while the checkout is still at its old path."""
    from pilot.config import AppConfig
    from pilot.internal.git import GitRepo

    return AppConfig(
        name=legacy_app.name,
        repo=legacy_app.repo,
        branch=legacy_app.branch or GitRepo(legacy_app.path).head_sha,
    )


def validate_legacy_apps(legacy: LegacyBench, on_progress: Callable[[str], None]) -> None:
    """Run the source checks on each app in place. Marketplace apps are trusted, as on install.
    `LegacyBench` has the paths these checks read from a `Bench`."""
    from typing import cast

    from pilot.core.app import App
    from pilot.core.app.validator import Validator, source_checks

    for legacy_app in legacy.apps:
        app = App(get_app_config(legacy_app), cast("Bench", legacy))
        if legacy_app.name == FRAMEWORK_APP or app.is_marketplace:
            continue
        on_progress(f"Validating {legacy_app.name}...")
        Validator(app, checks=source_checks()).validate()
