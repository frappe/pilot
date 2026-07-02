import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import TYPE_CHECKING

from pilot.core.marketplace import Marketplace

from .base_task import BaseTask

if TYPE_CHECKING:
    from pilot.core.app import App


class FetchAppUpdatesTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        # Index the registry by name once so each app is an O(1) lookup.
        self._marketplace_by_name = {app["name"]: app for app in Marketplace.registry()}

    def _matching_target(self, name: str, bench_app: "App") -> dict | None:
        """The marketplace target whose version equals this app's installed version, if any."""
        app = self._marketplace_by_name.get(name)
        if not app or bench_app.config.repo != app["repo"]:
            return None
        version = bench_app.installed_version
        return next((t for t in app["targets"] if t["version"] == version), None)

    def _check_update(self, name: str, bench_app: "App") -> bool:
        target = self._matching_target(name, bench_app)
        if target and target["target_type"] in ("tag", "commit"):
            # Marketplace entries only ever advance, so a different pin is
            # always a forward one — comparing against it locally is exact,
            # no network round trip needed.
            return not bench_app.is_on_revision(target)
        return bench_app.has_remote_update()

    def run(self) -> None:
        # No trailing "done" step: Sites.vue reads output[-1] as the JSON result,
        # so the dumped JSON must stay the last line.
        self._step("fetch", "Check for app updates")
        apps_dir = self.bench_root / "apps"
        app_names = [d.name for d in sorted(apps_dir.iterdir()) if d.is_dir() and (d / ".git").exists()]

        updates: dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self._check_update, name, self.bench.app(name)): name for name in app_names}
            for future in as_completed(futures):
                updates[futures[future]] = future.result()

        print(json.dumps(updates), flush=True)


if __name__ == "__main__":
    FetchAppUpdatesTask.main()
