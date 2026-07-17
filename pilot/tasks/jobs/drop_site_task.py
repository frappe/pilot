from pilot.core.site import Site
from pilot.tasks.jobs.base_task import BaseTask


class DropSiteTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("site")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.site = args.site

    def run(self) -> None:
        self._require_production_privileges()
        self._step("drop", f"Drop site {self.site}")
        Site.for_name(self.site, self.bench).drop(on_progress=self._report)
        self._step("done")


if __name__ == "__main__":
    DropSiteTask.main()
