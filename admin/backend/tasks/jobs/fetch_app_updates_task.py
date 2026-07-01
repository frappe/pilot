import json

from admin.backend.readers.app_reader import AppReader
from pilot.core.marketplace import Marketplace

from .base_task import BaseTask


class FetchAppUpdatesTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        # Index the registry by name once so each app is an O(1) lookup.
        self._marketplace_by_name = {app["name"]: app for app in Marketplace.registry()}

    def _is_pinned_by_marketplace(self, name: str) -> bool:
        """Whether the marketplace pins this app to a non-branch target.

        The target matching the app's installed version decides: a commit/tag
        target means the marketplace intends a fixed revision, so offering a
        branch update would move the app off it. Only branch targets are updatable.
        """
        app = self._marketplace_by_name.get(name)
        if not app:
            return False
        bench_app = self.bench.app(name)
        if bench_app.config.repo != app["repo"]:
            return False
        version = bench_app.installed_version
        return any(
            target["version"] == version and target["target_type"] != "branch"
            for target in app["targets"]
        )

    def run(self) -> None:
        # No trailing "done" step: Sites.vue reads output[-1] as the JSON result,
        # so the dumped JSON must stay the last line.
        self._step("fetch", "Check for app updates")
        apps_dir = self.bench_root / "apps"
        app_names = [d.name for d in sorted(apps_dir.iterdir()) if d.is_dir() and (d / ".git").exists()]
        apps_to_check = [name for name in app_names if not self._is_pinned_by_marketplace(name)]
        updates = AppReader(self.bench_root).check_remote_updates(apps_to_check)
        print(json.dumps(updates), flush=True)


if __name__ == "__main__":
    FetchAppUpdatesTask.main()
