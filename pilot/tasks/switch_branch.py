import sys
from dataclasses import dataclass, field
from typing import ClassVar

from pilot.exceptions import BenchError
from pilot.tasks import Task, on_success, step


@dataclass(kw_only=True)
class SwitchBranchTask(Task):
    command: ClassVar[str] = "switch-branch"
    # A cancel between checkout and reinstall leaves the env on a half-switched app.
    is_cancellable_while_running: ClassVar[bool] = False

    name: str
    branch: str
    # Sites with the app; they are backed up and migrated once the code has switched.
    sites: list[str] = field(default_factory=list)

    @classmethod
    def queue_switch(cls, bench, name: str, branch: str, idempotency_key: str | None = None) -> str:
        """Queue with the locks its site migration takes over: bench:update and each site with the app."""
        sites = bench.migrations.sites_for_apps({name})
        return cls.queue(
            bench,
            name=name,
            branch=branch,
            sites=sites,
            idempotency_key=idempotency_key,
            resource_key=cls.migration_locks(sites),
        )

    @staticmethod
    def migration_locks(sites: list[str]) -> list[str]:
        return ["bench:update", *(f"site:{site.lower()}" for site in sites)]

    def run(self) -> None:
        from pilot.managers.environment import PythonEnvManager

        self.require_migration_locks()
        app = self.bench.app(self.name)
        previous_branch, previous_sha = app.config.branch, app.head_sha
        env = PythonEnvManager(self.bench)
        self.checkout(app)
        is_installed = False
        try:
            self.validate(app)
            is_installed = True
            self.install(env, app)
            self.build_assets(env, app)
        except Exception:
            self.restore(env, app, previous_branch, previous_sha, is_installed)
            raise

        app.record_branch()
        print(f"'{self.name}' switched to '{self.branch}'.")
        self.queue_site_migrations(previous_branch, previous_sha, app.head_sha)

    def require_migration_locks(self) -> None:
        """Without these locks the final migration cannot start, so check before changing code."""
        from pilot.internal.tasks.store import TaskStore

        if not self.sites or not self.running_task_id:
            return
        held = set(TaskStore(self.bench_root).read_metadata(self.running_task_id).get("resource_keys") or [])
        if not set(self.migration_locks(self.sites)) <= held:
            raise BenchError("Queue a branch switch with SwitchBranchTask.queue_switch, which takes the site locks.")

    @on_success
    def reload_workers(self) -> dict:
        """Long-lived web and background workers hold the old app list and
        import map, so they need a restart once this task lands."""
        return {"web_only": False}

    @step("checkout", lambda self: f"Switch to branch '{self.branch}'")
    def checkout(self, app) -> None:
        try:
            app.switch_branch(self.branch)
        except BenchError as exc:
            print(str(exc))
            sys.exit(1)

    @step("validate", lambda self: f"Validate {self.name} on '{self.branch}'")
    def validate(self, app) -> None:
        app.validate()

    @step("install", lambda self: f"Reinstall {self.name}")
    def install(self, env, app) -> None:
        env.install_app(app)

    @step("assets", "Build assets")
    def build_assets(self, env, app) -> None:
        env.build_assets_for_app(app)

    @step("restore", lambda self: f"Return {self.name} to its previous branch")
    def restore(self, env, app, previous_branch: str, previous_sha: str, is_installed: bool) -> None:
        """The editable install makes a checkout live at once, so go straight back."""
        app.return_to(previous_branch, previous_sha)
        if is_installed:
            env.install_app(app)
            env.build_assets_for_app(app)

    @step("migrate", lambda self: f"Queue backup and migration for {len(self.sites)} site(s)")
    def queue_site_migrations(self, previous_branch: str, previous_sha: str, new_sha: str) -> None:
        """The migration takes over this task's locks, so no update starts in between. Its
        revert returns the app to the previous branch as well as the site databases."""
        from pilot.core.bench.migration.operation import AppRevision

        if not self.sites:
            return
        switched_app = AppRevision(name=self.name, sha=previous_sha, updated_sha=new_sha, branch=previous_branch)
        operation = self.bench.migrations.create_site_migrate(*self.sites, switched_app=switched_app)
        operation.begin(handoff_from=self.running_task_id or None)
        print(f"Queued migration {operation.id} for {', '.join(self.sites)}.")


if __name__ == "__main__":
    SwitchBranchTask.main()
