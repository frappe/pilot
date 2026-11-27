from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Any

from pilot.exceptions import AgentError, AgentRefusedError

# Raised when a route or a field changes meaning, so each VM refuses the other's older Pilot.
API_VERSION = 1


class AgentClient:
    """The Pilot agent on a VM that this host manages, which runs the operations on it that this host cannot."""

    request_timeout = 30
    # The agent spends 1 s sampling, and an unreachable VM must not hold up the monitor's tick.
    metrics_timeout = 3
    # A release download or a dependency install on the VM takes minutes.
    pilot_update_timeout = 20 * 60
    pilot_update_poll_interval = 3

    def __init__(self, host: str, port: int, token: str) -> None:
        self.host = host
        self.port = port
        self.token = token

    @property
    def base_url(self) -> str:
        """The agent's URL, with an IPv6 address in brackets."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    def request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> Any:
        """Call the agent and return its JSON answer; raise AgentError with the agent's reason."""
        request = urllib.request.Request(
            self.base_url + path,
            data=json.dumps(body).encode() if body is not None else None,
            method=method,
            headers={"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout or self.request_timeout) as response:
                return json.loads(response.read() or b"null")
        except urllib.error.HTTPError as exc:
            raise AgentRefusedError(
                f"The Pilot agent on {self.host} refused: {_error_detail(exc)}", exc.code
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AgentError(f"Pilot cannot reach the Pilot agent on {self.host}: {exc}") from exc

    def get_health(self) -> dict[str, Any]:
        """The VM's state and the health of each of its roles; raise when the agent speaks another API version."""
        health = self.request("GET", "/health")
        if health["api_version"] != API_VERSION:
            raise AgentError(
                f"The Pilot agent on {self.host} speaks API version {health['api_version']}, "
                f"and this Pilot speaks {API_VERSION}. Update Pilot on both VMs."
            )
        return health

    def get_host_metrics(self) -> dict[str, Any]:
        """One system metrics record of the VM, in the shape of the monitor's system log."""
        return self.request("GET", "/metrics", timeout=self.metrics_timeout)

    def update_pilot(self, on_progress: Callable[[str], None] = lambda message: None) -> dict[str, Any]:
        """Update Pilot on the VM and wait until it ends; raise with the agent's reason when it fails."""
        on_progress(f"Updating Pilot on {self.host}...")
        state = self.wait_for_pilot_update(self.start_pilot_update())
        if state["status"] != "succeeded":
            raise AgentError(f"Pilot on {self.host} did not update: {state['error']}")
        on_progress(f"Pilot on {self.host} updated: {state['version_before']} -> {state['version_after']}.")
        return state

    def start_pilot_update(self) -> dict[str, Any]:
        """Start an update of Pilot on the VM, or join the one that runs; return its state."""
        try:
            return self.request("POST", "/pilot/update")
        except AgentRefusedError as error:
            if error.status_code == 404:
                raise AgentError(
                    f"Pilot on {self.host} is too old to update itself. Update Pilot there by hand "
                    "once, run `systemctl --user restart pilot-agent.service` there, and try again."
                ) from error
            raise

    def get_pilot_update(self) -> dict[str, Any]:
        """The running or last update of Pilot on the VM."""
        return self.request("GET", "/pilot/update")

    def wait_for_pilot_update(self, state: dict[str, Any]) -> dict[str, Any]:
        """Poll a running update until it ends. Not through `get_health`: while it runs, the two VMs may speak
        different API versions."""
        deadline = time.monotonic() + self.pilot_update_timeout
        while state["status"] == "running":
            if time.monotonic() > deadline:
                raise AgentError(
                    f"Pilot on {self.host} did not finish updating in "
                    f"{self.pilot_update_timeout // 60} minutes. It may still run; try again to wait for it."
                )
            time.sleep(self.pilot_update_poll_interval)
            try:
                state = self.get_pilot_update()
            except AgentRefusedError:
                raise
            except AgentError:
                # The update ends by restarting the agent, which then answers nothing for a few seconds.
                continue
        return state


def _error_detail(error: urllib.error.HTTPError) -> str:
    """The agent's reason, or the status when it gave none."""
    try:
        return str(json.loads(error.read())["detail"])
    except (ValueError, KeyError, TypeError):
        return f"HTTP {error.code}"
