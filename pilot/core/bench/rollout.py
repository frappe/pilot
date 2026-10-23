from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from functools import cached_property
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.bench.migration.operation import AppRevision

# Datum rejects a log message over 8 KiB, and one character takes up to 4 bytes.
_LOG_MESSAGE_LIMIT = 2000


class AppRollout:
    """Offers each marketplace release to a share of servers that grows over time. Central
    owns the steps. The clock starts when the release was added to the registry, so every
    server reads the same time."""

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench
        self._settings_lock = threading.Lock()

    @property
    def settings(self) -> dict | None | Literal[False]:
        """Central's `start_percent`, `step_percent`, `interval_hours`, `max_percent` and
        `held_commits`. None when there is no rollout and every update is offered at once. False
        when Central cannot answer or sends bad steps, so new updates wait for it."""
        # The app update check asks from several threads at once, and Central is asked one time.
        with self._settings_lock:
            return self._central_settings

    @cached_property
    def _central_settings(self) -> dict | None | Literal[False]:
        from pilot.integrations.central.client import CentralClient, CentralClientError

        if not self.bench.config.central.enabled:
            return None

        try:
            reply = CentralClient().config()
        except CentralClientError as error:
            print(f"App updates wait for Central: {error}", flush=True)
            return False

        if not isinstance(reply, dict):
            print(f"App updates wait for Central: bad reply {reply!r}", flush=True)
            return False

        settings = reply.get("app_rollout")
        if settings is None:
            return None

        if not self._has_valid_steps(settings):
            print(f"App updates wait for Central: bad app rollout steps {settings!r}", flush=True)
            return False

        if not self._has_valid_holds(settings):
            print(f"App updates wait for Central: bad held commits {settings!r}", flush=True)
            return False

        return settings

    def is_offered(self, app_name: str, commit: str) -> bool:
        """Whether the release has reached this server's rollout number."""
        from pilot.core.registry_cache import RegistryCache
        from pilot.core.server import Server
        from pilot.utils import cli_root

        if self.settings is None:
            return True

        if self.settings is False:
            return False

        if commit in self.settings.get("held_commits", []):
            print(f"The {app_name} update waits: Central holds this release", flush=True)
            return False

        added_at = RegistryCache(cli_root()).get_added_at(app_name, commit)
        if added_at is None:
            print(f"The {app_name} update waits: the registry does not say when it was added", flush=True)
            return False

        return Server().get_rollout_number() <= self.get_percent(added_at)

    def get_percent(self, added_at: datetime) -> int:
        """The start percent plus one step for each full interval since the release was added,
        up to the max percent. A max percent of 0 stops the rollout."""
        settings = self.settings
        assert settings, "Only called when Central sent rollout steps."

        hours = (datetime.now(UTC) - added_at).total_seconds() / 3600
        # A clock set back in time must not drop below the start percent.
        steps = max(0, int(hours // settings["interval_hours"]))
        percent = settings["start_percent"] + steps * settings["step_percent"]
        return min(percent, settings["max_percent"])

    def log_updates(
        self, revisions: list["AppRevision"], status: Literal["success", "failed"], error: str = ""
    ) -> None:
        """One line per updated app in `logs/app_updates.json.log`, which fluent-bit ships to Datum."""
        from pilot.core.server import Server
        from pilot.exceptions import BenchError

        try:
            rollout_number = Server().get_rollout_number()
            with (self.bench.logs_path / "app_updates.json.log").open("a") as log_file:
                for revision in revisions:
                    line = {
                        "time": datetime.now(UTC).isoformat(),
                        "level": "info" if status == "success" else "error",
                        "message": error[:_LOG_MESSAGE_LIMIT] or status,
                        "status": status,
                        "app": revision.name,
                        "from_commit": revision.sha,
                        "to_commit": revision.target_sha,
                        "rollout_number": rollout_number,
                    }
                    log_file.write(json.dumps(line) + "\n")
        except (OSError, BenchError) as log_error:
            print(f"Could not write the app update log: {log_error}", flush=True)

    @staticmethod
    def _has_valid_steps(settings: object) -> bool:
        """Every step is a whole number. The percents are 1 to 100, and the max percent is 0 to 100."""
        if not isinstance(settings, dict):
            return False

        for key in ("start_percent", "step_percent", "interval_hours", "max_percent"):
            value = settings.get(key)
            # JSON true and false arrive as bool, and Python also counts a bool as an int.
            if isinstance(value, bool) or not isinstance(value, int):
                return False

        if settings["interval_hours"] < 1:
            return False

        for key in ("start_percent", "step_percent"):
            if not 1 <= settings[key] <= 100:
                return False

        return 0 <= settings["max_percent"] <= 100

    @staticmethod
    def _has_valid_holds(settings: dict) -> bool:
        """The held commits are optional, so an older Central that sends none means no holds."""
        held_commits = settings.get("held_commits", [])
        if not isinstance(held_commits, list):
            return False

        return all(isinstance(commit, str) for commit in held_commits)
