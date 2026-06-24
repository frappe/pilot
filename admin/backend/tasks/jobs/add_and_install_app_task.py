import subprocess
import sys
import time

from bench_cli.commands.get_app import GetAppCommand
from .base_task import BaseTask


def _step(key: str, label: str = "") -> None:
    print(f"##[step:{key},{time.time():.3f}] {label}", flush=True)


class AddAndInstallAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("repo")
        p.add_argument("name")
        p.add_argument("--branch", default="")
        p.add_argument("--sites", nargs="*", default=[])
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.repo = args.repo
        self.name = args.name
        self.branch = args.branch
        self.sites = args.sites or []

    def run(self) -> None:
        _step("fetch", f"Fetch {self.name}")
        cmd = GetAppCommand(self.bench, self.repo, self.branch)
        cmd.run()

        sites_dir = self.bench_root / "sites"
        for site in self.sites:
            safe_key = site.replace(".", "_").replace("-", "_")
            _step(f"install_{safe_key}", f"Install on {site}")
            result = subprocess.run(
                [*self.bench.frappe_call, "frappe", "--site", site, "install-app", cmd.app.module_name],
                cwd=str(sites_dir),
            )
            if result.returncode != 0:
                sys.exit(result.returncode)

        _step("build", f"Build assets for {self.name}")
        from bench_cli.managers.python_env_manager import PythonEnvManager
        PythonEnvManager(self.bench).build_assets_for_app(cmd.app)

        _step("done")


if __name__ == "__main__":
    AddAndInstallAppTask.main()
