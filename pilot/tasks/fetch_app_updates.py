import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import ClassVar

from pilot.integrations.marketplace import Marketplace

from pilot.tasks.base import BaseTask, step


@dataclass(kw_only=True)
class FetchAppUpdatesTask(BaseTask):
    command: ClassVar[str] = "fetch-all-app-updates"
    # Sites.vue reads output[-1] as the JSON result, so the dumped JSON must
    # stay the last line — no trailing "done" step.
    has_done_step: ClassVar[bool] = False

    def __post_init__(self) -> None:
        super().__post_init__()
        # Index the registry by name once so each app is an O(1) lookup.
        self.marketplace_by_name = {app["name"]: app for app in Marketplace.registry()}

    def run(self) -> None:
        updates = self.fetch()
        print(json.dumps(updates), flush=True)

    def check_update(self, name: str) -> bool:
        app = self.bench.app(name)
        return app.has_marketplace_update(self.marketplace_by_name.get(name))

    @step("fetch", "Check for app updates")
    def fetch(self) -> dict[str, bool]:
        apps_dir = self.bench_root / "apps"
        app_names = [
            d.name for d in sorted(apps_dir.iterdir()) if d.is_dir() and (d / ".git").exists()
        ]

        updates: dict[str, bool] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self.check_update, name): name for name in app_names}
            for future in as_completed(futures):
                updates[futures[future]] = future.result()
        return updates


if __name__ == "__main__":
    FetchAppUpdatesTask.main()
