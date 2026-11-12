from __future__ import annotations

import json
import time
from pathlib import Path


class SustainedAlerts:
    """Names the conditions that have held long enough to alert on. The file is
    the only record of how long each has been true, so one that recovers is
    dropped from it and the file goes away once nothing is active."""

    # How long a condition must hold before it is worth telling anyone about.
    window_seconds = 300

    def __init__(self, path: Path) -> None:
        self.path = path
        # Names whose alert went out and that are no longer active, set by `due()`.
        self.recovered: list[str] = []

    def due(self, active: list[str]) -> list[str]:
        """Record this round and return the names that have crossed the window.
        They stay due until mark_notified() confirms an alert actually went out,
        so a round where every sink was down is retried on the next tick."""
        previous = self.read_state()
        now = time.time()
        self.recovered = sorted(
            name for name, entry in previous.items() if entry["notified"] and name not in active
        )

        state = {}
        due = []
        for name in sorted(active):
            entry = previous.get(name) or {"since": now, "notified": False}
            if not entry["notified"] and now - entry["since"] >= self.window_seconds:
                due.append(name)
            state[name] = entry

        self.write_state(state)
        return due

    def mark_notified(self, names: list[str]) -> None:
        """Call only once an alert has been delivered. A name marked here does
        not alert again until it recovers and breaks a second time."""
        state = self.read_state()
        for name in names:
            if name in state:
                state[name]["notified"] = True
        self.write_state(state)

    def sustained(self) -> list[str]:
        """Every condition that has held for the full window, read from the state
        `due()` just wrote - so call it after `due()`, not instead of it.

        Unlike `due()` this ignores delivery entirely. A condition an external sink
        already accepted is still sustained, and still needs a local record if the
        bench never managed to write one."""
        now = time.time()
        return sorted(
            name for name, entry in self.read_state().items() if now - entry["since"] >= self.window_seconds
        )

    def unrecorded(self, names: list[str]) -> list[str]:
        """Of `names`, the ones the bench has not written to its own feed yet.

        Delivery and recording are different facts. A due condition stays due until
        some sink accepts it, so the delivery attempt repeats every tick - the local
        record must not, or a bench with no reachable sink writes one row per tick
        for as long as the condition holds."""
        state = self.read_state()
        return [name for name in names if not state.get(name, {}).get("recorded")]

    def mark_recorded(self, names: list[str]) -> None:
        """Call once the bench's own feed holds a record for these conditions."""
        state = self.read_state()
        for name in names:
            if name in state:
                state[name]["recorded"] = True
        self.write_state(state)

    def read_state(self) -> dict[str, dict]:
        """The stored conditions. A hand-edited or truncated file starts the window over rather than
        stopping the daemon."""
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, ValueError):
            return {}

    def write_state(self, state: dict[str, dict]) -> None:
        """Store the conditions; remove the file once none are active."""
        if not state:
            self.path.unlink(missing_ok=True)
            return
        self.path.write_text(json.dumps(state))
