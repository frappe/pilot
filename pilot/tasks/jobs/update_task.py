import sys

from pilot.commands.runtime.update import UpdateCommand
from pilot.exceptions import MigrateError
from pilot.tasks.jobs.base_task import BaseTask


class UpdateTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("--apps", nargs="*", default=None)
        p.add_argument("--skip-failing-patches", action="store_true")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self._apps_filter = set(args.apps) if args.apps else None
        self._skip_failing_patches = args.skip_failing_patches

    def run(self) -> None:
        try:
            UpdateCommand(
                self.bench,
                skip_confirm=True,
                apps=self._apps_filter,
                skip_failing_patches=self._skip_failing_patches,
            ).run()
        except MigrateError:
            sys.exit(1)


if __name__ == "__main__":
    UpdateTask.main()
