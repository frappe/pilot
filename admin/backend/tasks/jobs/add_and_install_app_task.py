import subprocess
import sys
import time

from pilot.commands.get_app import GetAppCommand
from pilot.exceptions import BenchError

from .base_task import BaseTask


def _step(key: str, label: str = "") -> None:
    print(f"##[step:{key},{time.time():.3f}] {label}", flush=True)


class AddAndInstallAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("--repo", default="")
        p.add_argument("--branch", default="")
        p.add_argument("--marketplace-app", default="")
        p.add_argument("--sites", nargs="*", default=[])
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.repo = args.repo
        self.branch = args.branch
        self.marketplace_app = args.marketplace_app
        self.sites = args.sites or []

    def run(self) -> None:
        if self.marketplace_app:
            self._install_from_marketplace()
        else:
            self._install_custom()

    def _install_custom(self) -> None:
        _step("fetch", f"Fetch {self.repo}")
        cmd = GetAppCommand(self.bench, self.repo, self.branch)
        cmd.run()
        self._install_on_sites([cmd])
        _step("done")

    def _install_from_marketplace(self) -> None:
        from pilot.core.marketplace import Marketplace

        apps = Marketplace(self.bench).read_all_apps()
        resolver = next((a for a in apps if a.app == self.marketplace_app), None)
        if not resolver:
            raise BenchError(f"'{self.marketplace_app}' not found in marketplace.")
        cmds = []
        for dep in resolver.resolve():
            _step("fetch", f"Fetch {dep.app}")
            cmd = GetAppCommand(self.bench, dep.repo, dep.target)
            cmd.run()
            cmds.append(cmd)
        self._install_on_sites(cmds)
        _step("done")

    def _install_on_sites(self, cmds: list) -> None:
        from pilot.managers.python_env_manager import PythonEnvManager

        sites_dir = self.bench_root / "sites"
        for site in self.sites:
            safe_key = site.replace(".", "_").replace("-", "_")
            for cmd in cmds:
                _step(f"install_{safe_key}_{cmd.app.config.name}", f"Install {cmd.app.config.name} on {site}")
                result = subprocess.run(
                    [*self.bench.frappe_call, "frappe", "--site", site, "install-app", cmd.app.config.name],
                    cwd=str(sites_dir),
                )
                if result.returncode != 0:
                    sys.exit(result.returncode)
        env = PythonEnvManager(self.bench)
        for cmd in cmds:
            _step("build", f"Build assets for {cmd.app.config.name}")
            env.build_assets_for_app(cmd.app)


if __name__ == "__main__":
    AddAndInstallAppTask.main()
