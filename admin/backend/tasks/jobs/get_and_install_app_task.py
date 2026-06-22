import time

from bench_cli.commands.get_app import GetAppCommand
from bench_cli.core.bench import Bench, BenchConfig
from bench_cli.core.site import Site, SiteConfig

from .base_task import BaseTask


def _step(key: str, label: str = "") -> None:
    print(f"##[step:{key},{time.time():.3f}] {label}", flush=True)


class GetAndInstallAppTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        p.add_argument("app")
        p.add_argument("repo")
        p.add_argument("--branch", default="")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site
        self.app = args.app
        self.repo = args.repo
        self.branch = args.branch

    def run(self) -> None:
        _step("fetch", f"Fetch {self.app}")
        GetAppCommand(self.bench, self.repo, self.branch).run()

        _step("install", f"Install on {self.site}")
        bench = Bench(BenchConfig.from_file(self.bench_root / "bench.toml"), self.bench_root)
        site = Site(SiteConfig(name=self.site, apps=[]), bench)
        site.install_app(self.app)

        _step("done")


if __name__ == "__main__":
    GetAndInstallAppTask.main()
