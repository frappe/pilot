from __future__ import annotations

import re
import tomllib
from collections.abc import Callable
from functools import cached_property
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError, ConfigError
from pilot.utils import benches_dir

if TYPE_CHECKING:
    from pilot.config import BenchConfig
    from pilot.core.bench import Bench
    from pilot.core.site import Site

ADMIN_PASSWORD = "admin"
DEFAULT_DB_TYPE = "sqlite"


class Workspace:
    """A Frappe app repository developed on its own bench. The bench lives in the
    benches directory and links the repository into apps/, so the repository
    stays the developer's own checkout."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    @classmethod
    def find(cls, start: Path | None = None) -> "Workspace | None":
        """The app repository containing `start` (default: the current directory)."""
        start = (start or Path.cwd()).resolve()
        for directory in (start, *start.parents):
            workspace = cls(directory)
            if workspace.is_frappe_app:
                return workspace
        return None

    @classmethod
    def current(cls) -> "Workspace":
        workspace = cls.find()
        if workspace is None:
            raise BenchError(
                "No Frappe app found here. Run this inside an app repository, next to its pyproject.toml."
            )
        return workspace

    @cached_property
    def app_name(self) -> str:
        """The app's importable package name: the project name in pyproject.toml,
        or else the only package next to pyproject.toml that holds a hooks.py."""
        pyproject = self.root / "pyproject.toml"
        if not pyproject.is_file():
            return ""
        try:
            project = tomllib.loads(pyproject.read_text()).get("project", {})
        except (OSError, tomllib.TOMLDecodeError):
            project = {}
        if name := project.get("name"):
            return str(name).replace("-", "_")
        packages = [hooks.parent.name for hooks in self.root.glob("*/hooks.py")]
        return packages[0] if len(packages) == 1 else ""

    @property
    def is_frappe_app(self) -> bool:
        return bool(self.app_name) and (self.root / self.app_name / "hooks.py").exists()

    @property
    def default_frappe_branch(self) -> str:
        """The app's own `version-N` branch, or develop for any other branch."""
        from pilot.internal.git import GitRepo

        branch = GitRepo(self.root).branch
        return branch if re.fullmatch(r"version-\d+", branch) else "develop"

    @cached_property
    def bench(self) -> "Bench":
        bench = self.find_bench()
        if bench is None:
            raise BenchError(f"{self.root} has no bench yet. Run: pilot dev setup")
        return bench

    def find_bench(self) -> "Bench | None":
        """The bench that links this repository. Reads the bench that setup recorded
        in the git directory, and scans the benches directory when that record is
        missing or stale. A broken bench.toml fails the lookup only when it is recorded."""
        from pilot.core.bench import Bench

        name = self.recorded_bench_name
        if name and self.is_linked_by(benches_dir() / name):
            return Bench(benches_dir() / name)
        for toml_path in sorted(benches_dir().glob("*/bench.toml")):
            try:
                if self.is_linked_by(toml_path.parent):
                    return Bench(toml_path.parent)
            except ConfigError:
                continue
        return None

    def is_linked_by(self, bench_root: Path) -> bool:
        from pilot.config import BenchConfig

        toml_path = bench_root / "bench.toml"
        return toml_path.exists() and self.root in BenchConfig.get_linked_app_paths(bench_root)

    @cached_property
    def bench_record(self) -> Path | None:
        """The file in the repository's git directory that names its bench."""
        from pilot.internal.git import GitRepo

        repo = GitRepo(self.root)
        if not repo.is_cloned:
            return None
        git_dir = repo.git_dir
        return git_dir / "pilot-bench" if git_dir.is_absolute() else None

    @property
    def recorded_bench_name(self) -> str:
        try:
            return self.bench_record.read_text().strip() if self.bench_record else ""
        except OSError:
            return ""

    @property
    def site(self) -> "Site":
        return self.bench.site(f"{self.bench.config.name}.localhost")

    @property
    def site_url(self) -> str:
        return f"http://{self.site.config.name}:{self.bench.config.http_port}"

    @property
    def is_running(self) -> bool:
        from pilot.managers.processes.local import ProcessManager

        return ProcessManager(self.bench).is_running()

    def setup(
        self,
        db_type: str | None = None,
        frappe_branch: str | None = None,
        on_progress: Callable[[str], None] = lambda message: None,
        from_site: str | None = None,
    ) -> "Site":
        """Create, initialise, and provision the bench, or bring an existing one
        up to date with the repository. Safe to run again."""
        if self.app_name == "frappe":
            raise BenchError("Pilot dev cannot develop the frappe framework itself yet.")
        source = self.get_clone_source(from_site, db_type)
        db_type = source.db_type if source else db_type
        self.bench = self.find_bench() or self.create_bench(
            db_type or DEFAULT_DB_TYPE, frappe_branch or self.default_frappe_branch, on_progress
        )
        if self.bench_record:
            self.bench_record.write_text(f"{self.bench.path.name}\n")
        self.ensure_matches(db_type, frappe_branch)
        self.initialize_site(source, on_progress)
        self.configure_site()
        return self.site

    def initialize_site(self, source: "Site | None", on_progress: Callable[[str], None]) -> None:
        if source and self.site.exists:
            raise BenchError("The workspace site already exists. Run setup without --from-site to update it.")
        self.initialize_bench(on_progress)
        self.restore_app(on_progress)
        if source:
            self.clone_site(source, on_progress)
        elif self.site.exists:
            self.sync(on_progress)
        else:
            self.create_site(on_progress)

    def get_clone_source(self, value: str | None, db_type: str | None) -> "Site | None":
        from pilot.config import BenchConfig
        from pilot.core.bench import Bench
        from pilot.internal.validators import validate_site_name

        if value is None:
            return None
        name, separator, site_name = value.partition("/")
        if not separator or validate_site_name(site_name):
            raise BenchError("Use --from-site BENCH/SITE.")
        BenchConfig.default(name).validate()
        path = benches_dir() / name
        if not (path / "bench.toml").is_file():
            raise BenchError(f"Source bench '{name}' does not exist.")
        source = Bench(path).site(site_name)
        if not source.exists:
            raise BenchError(f"Source site '{value}' does not exist.")
        if source.db_type not in ("mariadb", "postgres"):
            raise BenchError("Site copying currently supports MariaDB and PostgreSQL.")
        if db_type and db_type != source.db_type:
            raise BenchError(f"Source site uses '{source.db_type}', not '{db_type}'.")
        return source

    def clone_site(self, source: "Site", on_progress: Callable[[str], None]) -> None:
        from pilot.internal.patch_state import mark_applied
        from pilot.patches.enroll_sites_in_scheduled_backups import PATCH_NAME

        source.clone(self.site.config.name, self.bench, ADMIN_PASSWORD, on_progress)
        mark_applied(self.bench.path, PATCH_NAME)
        self.sync(on_progress)

    def initialize_bench(self, on_progress: Callable[[str], None]) -> None:
        """Initialize a new bench, or resume an initialization that did not finish."""
        if not self.bench.python.exists():
            self.bench.initialize(on_progress=on_progress)
        elif not self.bench.is_initialized:
            self.resume_initialization(on_progress)

    def resume_initialization(self, on_progress: Callable[[str], None]) -> None:
        """Run initialization again for a bench whose initialization an earlier setup
        did not finish, for example after Ctrl-C. Its steps skip work that is already done."""
        name = self.bench.config.name
        on_progress(f"Resuming the setup of bench '{name}'...")
        try:
            self.bench.initialize(on_progress=on_progress)
        except Exception as error:
            raise BenchError(
                f"Bench '{name}' is incomplete and could not be resumed: {error}\n"
                "Run 'pilot dev drop', then 'pilot dev setup'."
            ) from error

    def create_bench(self, db_type: str, frappe_branch: str, on_progress: Callable[[str], None]) -> "Bench":
        from pilot.config import AppConfig
        from pilot.core.bench import Bench

        def configure(config: BenchConfig) -> None:
            config.admin.enabled = False
            config.allow_developer_mode = True
            config.apps.append(AppConfig(name=self.app_name, repo="", branch="", path=str(self.root)))

        name = self.next_bench_name
        on_progress(f"Creating bench '{name}' ({db_type}, frappe {frappe_branch}) for {self.root}")
        return Bench.create_at(
            benches_dir() / name, name, db_type=db_type, frappe_branch=frappe_branch, configure=configure
        )

    @property
    def next_bench_name(self) -> str:
        slug = re.sub(r"[^a-z0-9]+", "-", self.root.name.lower()).strip("-") or "app"
        name = base = f"dev-{slug}"
        suffix = 2
        while (benches_dir() / name).exists():
            name = f"{base}-{suffix}"
            suffix += 1
        return name

    def ensure_matches(self, db_type: str | None, frappe_branch: str | None) -> None:
        """An existing bench keeps its database and framework branch. Changing either
        means starting over, which deletes the site's data."""
        bench = self.bench
        current = {"database": bench.config.db_type, "frappe branch": bench.config.framework_app.branch}
        requested = {"database": db_type, "frappe branch": frappe_branch}
        for label, value in requested.items():
            if value and value != current[label]:
                raise BenchError(
                    f"Bench '{bench.config.name}' uses {label} '{current[label]}', not '{value}'. "
                    "Run 'pilot dev drop' first to start over."
                )

    def restore_app(self, on_progress: Callable[[str], None]) -> None:
        """Link and register the app again when `pilot remove-app` took it out of the bench."""
        from pilot.core.app import App

        bench = self.bench
        app = App(bench.config.get_app_by_name(self.app_name), bench)
        if app.path.is_symlink() and bench.is_app_installed(self.app_name):
            return
        on_progress(f"Linking {self.app_name} from {self.root}...")
        app.install_linked()

    def create_site(self, on_progress: Callable[[str], None]) -> "Site":
        """Create the dev site without scheduled backups. The bench is marked as enrolled
        already, so a later `pilot update` does not add them."""
        from pilot.core.site import Site
        from pilot.internal.patch_state import mark_applied
        from pilot.patches.enroll_sites_in_scheduled_backups import PATCH_NAME

        mark_applied(self.bench.path, PATCH_NAME)
        return Site.provision(
            self.bench,
            self.site.config.name,
            [self.app_name],
            ADMIN_PASSWORD,
            on_progress=on_progress,
            schedule_backups=False,
        )

    def configure_site(self) -> None:
        """Turn on developer mode, and make this the site that Frappe commands
        without --site use."""
        from pilot.config.common_site_config import update_common_site_config

        self.site.set_config_values({"developer_mode": 1})
        with update_common_site_config(self.bench.sites_path) as config:
            config["default_site"] = self.site.config.name

    def sync(self, on_progress: Callable[[str], None]) -> None:
        """Catch up with a pulled repository: new requirements, schema changes, and
        the app's assets. Also finishes the site's setup if an interrupted setup
        left the site without the app."""
        from pilot.managers.environment import PythonEnvManager
        from pilot.managers.redis import RedisManager

        site = self.site
        app = self.bench.app(self.app_name)
        self.bench.install_requirements(on_progress)
        with RedisManager(self.bench.config.redis, self.bench).running():
            if self.app_name not in site.installed_apps():
                site.finish_provisioning([self.app_name], on_progress)
            on_progress(f"Migrating {site.config.name}...")
            site.migrate()
        PythonEnvManager(self.bench).build_assets_for_app(app)

    def start(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        """Run the bench in the foreground until interrupted, as `pilot start` does."""
        bench = self.bench
        if not bench.is_initialized or not self.site.exists:
            raise BenchError(f"Bench '{bench.config.name}' is not set up yet. Run: pilot dev setup")
        if self.is_running:
            raise BenchError(f"Bench '{bench.config.name}' is already running.")
        on_progress(f"\n  Site   {self.site_url}")
        on_progress(f"  Login  Administrator / {ADMIN_PASSWORD}\n")
        bench.start(on_progress)

    def drop(self, on_progress: Callable[[str], None] = lambda message: None) -> None:
        """Delete the bench and its site. The repository is left untouched."""
        if self.is_running:
            raise BenchError(f"Bench '{self.bench.config.name}' is running. Stop 'pilot dev start' first.")
        if self.site.exists:
            self.site.drop(on_progress, no_backup=True)
        self.bench.drop(on_progress)
        if self.bench_record:
            self.bench_record.unlink(missing_ok=True)
