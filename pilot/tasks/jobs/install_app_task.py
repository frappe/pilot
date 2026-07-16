import subprocess
import sys

from .base_task import BaseTask


class InstallAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        p.add_argument("app")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site
        self.app = args.app

    def run(self) -> None:
        sites_dir = self.bench_root / "sites"
        app = self.bench.app(self.app)

        self._step("install", f"Install {self.app} into {self.site}")
        result = subprocess.run(
            [*self.bench.frappe_call, "frappe", "--site", self.site, "install-app", app.config.name],
            cwd=str(sites_dir),
        )
        if result.returncode != 0:
            sys.exit(result.returncode)

        self._step("assets", f"Build assets for {self.app}")
        from pilot.managers.python_env_manager import PythonEnvManager
        PythonEnvManager(self.bench).build_assets_for_app(app)
        self._step("done")


if __name__ == "__main__":
    InstallAppTask.main()
