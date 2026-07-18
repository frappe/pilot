import sys
from dataclasses import dataclass
from typing import ClassVar

from pilot.tasks import Task, step


@dataclass(kw_only=True)
class SwitchBranchTask(Task):
    command: ClassVar[str] = "switch-branch"

    name: str
    branch: str

    def run(self) -> None:
        from pilot.managers.environment import PythonEnvManager

        app = self.bench.app(self.name)
        self.checkout(app)

        env = PythonEnvManager(self.bench)
        self.install(env, app)
        self.build_assets(env, app)

        self.update_bench_toml_branch()
        print(f"'{self.name}' switched to '{self.branch}' successfully.")

    @step("checkout", lambda self: f"Switch to branch '{self.branch}'")
    def checkout(self, app) -> None:
        from pilot.exceptions import BenchError

        try:
            app.switch_branch(self.branch)
        except BenchError as exc:
            print(str(exc))
            sys.exit(1)

    @step("install", lambda self: f"Reinstall {self.name}")
    def install(self, env, app) -> None:
        env.install_app(app)

    @step("assets", "Build assets")
    def build_assets(self, env, app) -> None:
        env.build_assets_for_app(app)

    def update_bench_toml_branch(self) -> None:
        from pilot.config import BenchConfig

        with BenchConfig.open(self.bench_root, mode="raw") as raw:
            for app_entry in raw.get("apps", []):
                if app_entry.get("name") == self.name:
                    app_entry["branch"] = self.branch
                    break


if __name__ == "__main__":
    SwitchBranchTask.main()
