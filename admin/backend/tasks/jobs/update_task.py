import sys
from pathlib import Path

from pilot.commands.update import UpdateCommand
from pilot.exceptions import MigrateError
from .base_task import BaseTask


class UpdateTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("--apps", nargs="*", default=None)
        p.add_argument("--task-log", default=None)
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self._apps_filter = set(args.apps) if args.apps else None
        self._task_log = Path(args.task_log) if getattr(args, "task_log", None) else None

    def run(self) -> None:
        try:
            UpdateCommand(
                self.bench,
                skip_confirm=True,
                apps=self._apps_filter,
                task_log=self._task_log,
            ).run()
        except MigrateError:
            sys.exit(1)


if __name__ == "__main__":
    UpdateTask.main()
