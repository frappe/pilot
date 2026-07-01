import json

from admin.backend.readers.app_reader import AppReader

from .base_task import BaseTask


class FetchAppUpdatesTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)

    def run(self) -> None:
        # No trailing "done" step: Sites.vue reads output[-1] as the JSON result,
        # so the dumped JSON must stay the last line.
        self._step("fetch", "Check for app updates")
        apps_dir = self.bench_root / "apps"
        app_names = [d.name for d in sorted(apps_dir.iterdir()) if d.is_dir() and (d / ".git").exists()]
        updates = AppReader(self.bench_root).check_remote_updates(app_names)
        print(json.dumps(updates), flush=True)


if __name__ == "__main__":
    FetchAppUpdatesTask.main()
