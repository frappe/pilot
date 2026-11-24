from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from pilot.exceptions import DatabaseError

if TYPE_CHECKING:
    from pilot.config import MariaDBConfig

# Raised when a route or a field changes meaning, so each VM refuses the other's older Pilot.
API_VERSION = 1


class DatabaseAgentRefusedError(DatabaseError):
    """The agent answered a request with an HTTP error."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class DatabaseAgentClient:
    """The database agent on a remote MariaDB's VM, which runs the operations on it that SQL cannot."""

    request_timeout = 30
    # The agent spends 1 s sampling, and an unreachable VM must not hold up the monitor's tick.
    metrics_timeout = 3
    # A release download or a dependency install on the database VM takes minutes.
    pilot_update_timeout = 20 * 60
    pilot_update_poll_interval = 3

    def __init__(self, host: str, port: int, token: str) -> None:
        self.host = host
        self.port = port
        self.token = token

    @classmethod
    def for_config(cls, config: MariaDBConfig) -> DatabaseAgentClient:
        """The agent beside the configured remote server."""
        return cls(config.host, config.agent_port, config.agent_token)

    @property
    def base_url(self) -> str:
        """The agent's URL, with an IPv6 address in brackets."""
        host = f"[{self.host}]" if ":" in self.host else self.host
        return f"http://{host}:{self.port}"

    def request(self, method: str, path: str, body: dict | None = None, timeout: float | None = None) -> Any:
        """Call the agent and return its JSON answer; raise DatabaseError with the agent's reason."""
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
            raise DatabaseAgentRefusedError(
                f"The database agent on {self.host} refused: {_error_detail(exc)}", exc.code
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise DatabaseError(f"Pilot cannot reach the database agent on {self.host}: {exc}") from exc

    def get_health(self) -> dict[str, Any]:
        """The server's state; raise when the agent speaks another API version."""
        health = self.request("GET", "/health")
        if health["api_version"] != API_VERSION:
            raise DatabaseError(
                f"The database agent on {self.host} speaks API version {health['api_version']}, "
                f"and this Pilot speaks {API_VERSION}. Update Pilot on both VMs."
            )
        return health

    def read_managed_config(self) -> str:
        """The variable editor's option file."""
        return self.request("GET", "/config/managed")["content"]

    def write_managed_config(self, content: str) -> None:
        """Replace the variable editor's option file."""
        self.request("PUT", "/config/managed", {"content": content})

    def control(self, action: str, timeout: float) -> None:
        """Start, stop or restart the server."""
        self.request("POST", f"/service/{action}", timeout=timeout)

    def get_file(self, path: str, tail_bytes: int = 0) -> dict[str, Any]:
        """Whether a server file exists, its size, and its last `tail_bytes` as text."""
        query = urllib.parse.urlencode({"path": path, "tail_bytes": tail_bytes})
        return self.request("GET", f"/files?{query}")

    def get_free_disk_bytes(self) -> int:
        """Free bytes on the disk that holds the data directory."""
        return self.request("GET", "/disk")["free_bytes"]

    def get_host_metrics(self) -> dict[str, Any]:
        """One system metrics record of the database VM, in the shape of the monitor's system log."""
        return self.request("GET", "/metrics", timeout=self.metrics_timeout)

    def update_pilot(self, on_progress: Callable[[str], None] = lambda message: None) -> dict[str, Any]:
        """Update Pilot on the database VM and wait until it ends; raise with the agent's reason when it fails."""
        on_progress(f"Updating Pilot on the database VM {self.host}...")
        state = self.wait_for_pilot_update(self.start_pilot_update())
        if state["status"] != "succeeded":
            raise DatabaseError(f"Pilot on the database VM {self.host} did not update: {state['error']}")
        on_progress(f"Pilot on {self.host} updated: {state['version_before']} -> {state['version_after']}.")
        return state

    def start_pilot_update(self) -> dict[str, Any]:
        """Start an update of Pilot on the database VM, or join the one that runs; return its state."""
        try:
            return self.request("POST", "/pilot/update")
        except DatabaseAgentRefusedError as error:
            if error.status_code == 404:
                raise DatabaseError(
                    f"Pilot on the database VM {self.host} is too old to update itself. Update Pilot there by hand "
                    "once, run `systemctl --user restart pilot-database-agent.service` there, and try again."
                ) from error
            raise

    def get_pilot_update(self) -> dict[str, Any]:
        """The running or last update of Pilot on the database VM."""
        return self.request("GET", "/pilot/update")

    def wait_for_pilot_update(self, state: dict[str, Any]) -> dict[str, Any]:
        """Poll a running update until it ends. Not through `get_health`: while it runs, the two VMs may speak
        different API versions."""
        deadline = time.monotonic() + self.pilot_update_timeout
        while state["status"] == "running":
            if time.monotonic() > deadline:
                raise DatabaseError(
                    f"Pilot on the database VM {self.host} did not finish updating in "
                    f"{self.pilot_update_timeout // 60} minutes. It may still run; try again to wait for it."
                )
            time.sleep(self.pilot_update_poll_interval)
            try:
                state = self.get_pilot_update()
            except DatabaseAgentRefusedError:
                raise
            except DatabaseError:
                # The update ends by restarting the agent, which then answers nothing for a few seconds.
                continue
        return state

    def grant_admin(self, user: str, password: str) -> str:
        """Create or reset the admin account for this host's address; return that address."""
        return self.request("POST", "/accounts/admin", {"user": user, "password": password})["host"]


def _error_detail(error: urllib.error.HTTPError) -> str:
    """The agent's reason, or the status when it gave none."""
    try:
        return str(json.loads(error.read())["detail"])
    except (ValueError, KeyError, TypeError):
        return f"HTTP {error.code}"
