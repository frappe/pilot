import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, on_success, step


@dataclass(kw_only=True)
class SwitchBranchTask(Task):
    command: ClassVar[str] = "switch-branch"

    name: str
    branch: str

    def run(self) -> None:
        from pilot.exceptions import BenchError
        from pilot.internal.validators import validate_app_name, validate_branch_name
        from pilot.managers.environment import PythonEnvManager

        if error := validate_app_name(self.name):
            raise BenchError(error)
        if not self.branch:
            raise BenchError("Branch is required.")
        if error := validate_branch_name(self.branch):
            raise BenchError(error)

        app = self.bench.app(self.name)
        previous_branch, previous_sha = app.current_branch, app.head_sha
        self.checkout(app)
        self.validate(app, previous_branch, previous_sha)

        env = PythonEnvManager(self.bench)
        self.install(env, app)
        self.build_assets(env, app)

        app.record_branch()
        print(f"'{self.name}' switched to '{self.branch}' successfully.")

    @on_success
    def reload_workers(self) -> dict:
        """Long-lived web and background workers hold the old app list and
        import map, so they need a restart once this task lands."""
        return {"web_only": False}

    @step("checkout", lambda self: f"Switch to branch '{self.branch}'")
    def checkout(self, app) -> None:
        from pilot.exceptions import BenchError

        try:
            app.switch_branch(self.branch)
        except BenchError as exc:
            print(str(exc))
            sys.exit(1)

    @step("validate", lambda self: f"Validate {self.name} on '{self.branch}'")
    def validate(self, app, previous_branch: str, previous_sha: str) -> None:
        """The env installs the app editable, so the branch is live the moment it's
        checked out - a branch that fails the checks has to go back.

        Restore the branch rather than its commit: a detached HEAD would disagree
        with the branch bench.toml records. Any BenchError rolls back, not just a
        validation failure - uv falling over leaves the same live bad branch.
        """
        from pilot.exceptions import BenchError

        try:
            app.validate()
        except BenchError:
            if previous_branch:
                app.switch_branch(previous_branch)
            else:
                app.checkout_commit(previous_sha)  # it was already detached
            raise

    @step("install", lambda self: f"Reinstall {self.name}")
    def install(self, env, app) -> None:
        env.install_app(app)

    @step("assets", "Build assets")
    def build_assets(self, env, app) -> None:
        env.build_assets_for_app(app)


if __name__ == "__main__":
    SwitchBranchTask.main()
