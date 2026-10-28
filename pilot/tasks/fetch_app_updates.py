import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import ClassVar

from pilot.core.app.revisions import RevisionPin
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class FetchAppUpdatesTask(Task):
    command: ClassVar[str] = "fetch-all-app-updates"
    timeout_seconds: ClassVar[int] = 300
    # Polled frequently by the UI and read-only; keep it out of the audit log.
    audit_on_queue: ClassVar[bool] = False
    is_listed: ClassVar[bool] = False
    # Sites.vue reads output[-1] as the JSON result, so the dumped JSON must
    # stay the last line - no trailing "done" step.
    has_done_step: ClassVar[bool] = False

    def run(self) -> None:
        updates = self.fetch()
        print(json.dumps(updates), flush=True)

    def app_update(self, name: str) -> dict | None:
        """The pending update for an app as {current, target} labels and whether it needs a
        migrate, or None if up to date."""
        app = self.bench.app(name)
        pin = app.update_target()
        if pin is None or app.is_on_revision(pin):
            return None
        return {**self._update_labels(app, pin), "has_migrate_changes": app.has_migrate_changes(pin)}

    def _update_labels(self, app, pin: RevisionPin) -> dict:
        """Versions ('15.116.0 -> 15.117.0') when the target is a registry release, commit
        labels otherwise, such as a `follow_branch` app moving to its branch tip."""
        sha = app.installed_hash
        labels = {
            "current": sha[:10] if sha else "",
            "target": pin.ref[:10] if pin.kind == "commit" else pin.ref,
        }
        installed = app.installed_version
        advertised = self.get_release_version(app, pin)
        if installed and advertised and installed != advertised:
            labels["current"], labels["target"] = installed, advertised
        return labels

    def get_release_version(self, app, pin: RevisionPin) -> str:
        """The version the registry publishes at the pin's commit on this app's branch, or ''."""
        from pilot.integrations.marketplace import Marketplace

        entry = app.marketplace_entry
        if not entry:
            return ""
        return next(
            (
                release["version"]
                for release in Marketplace.releases(entry["name"])
                if release.get("branch") == app.config.branch and release.get("commit") == pin.ref
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
