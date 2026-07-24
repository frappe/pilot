import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import ClassVar

from pilot.core.app.revisions import RevisionPin
from pilot.integrations.git.base import same_repository
from pilot.integrations.marketplace import Marketplace
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class FetchAppUpdatesTask(Task):
    command: ClassVar[str] = "fetch-all-app-updates"
    # Sites.vue reads output[-1] as the JSON result, so the dumped JSON must
    # stay the last line - no trailing "done" step.
    has_done_step: ClassVar[bool] = False

    def __post_init__(self) -> None:
        super().__post_init__()
        # Index the registry by name once so each app is an O(1) lookup.
        self.marketplace_by_name = {app["name"]: app for app in Marketplace.registry()}

    def run(self) -> None:
        updates = self.fetch()
        print(json.dumps(updates), flush=True)

    def app_update(self, name: str) -> dict | None:
        """The pending update for an app as {current, target} labels, or None if up to date."""
        app = self.bench.app(name)
        pin = app.update_target(self.marketplace_by_name.get(name))
        if pin is None or app.is_on_revision(pin):
            return None
        return self._update_labels(app, pin)

    def _update_labels(self, app, pin: RevisionPin) -> dict:
        """Marketplace apps display versions ('15.116.0 -> 15.117.0'); other apps
        keep commit labels. Falls back to commits when no version line matches."""
        sha = app.installed_hash
        labels = {
            "current": sha[:10] if sha else "",
            "target": pin.ref[:10] if pin.kind == "commit" else pin.ref,
        }
        installed = app.installed_version
        advertised = self._marketplace_target_version(app)
        if installed and advertised and installed != advertised:
            labels["current"], labels["target"] = installed, advertised
        return labels

    def _marketplace_target_version(self, app) -> str:
        """The version the marketplace advertises for this app's branch line."""
        entry = self.marketplace_by_name.get(app.config.name)
        if not entry or not same_repository(app.config.repo, entry.get("repo", "")):
            return ""
        return next(
            (
                target["version"]
                for target in entry.get("targets") or []
                if target.get("target_type") == "branch" and target.get("target") == app.config.branch
            ),
            "",
        )

    @step("fetch", "Check for app updates")
    def fetch(self) -> dict[str, dict]:
        apps_dir = self.bench_root / "apps"
        app_names = [d.name for d in sorted(apps_dir.iterdir()) if d.is_dir() and (d / ".git").exists()]

        updates: dict[str, dict] = {}
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = {pool.submit(self.app_update, name): name for name in app_names}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    updates[futures[future]] = result
        return updates


if __name__ == "__main__":
    FetchAppUpdatesTask.main()
