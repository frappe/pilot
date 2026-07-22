from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class CentralClientError(BenchError):
    """A Central API call could not be made or was rejected."""


def _message(payload: Any) -> Any:
    if isinstance(payload, dict) and "message" in payload:
        return payload["message"]
    return payload


class CentralClient:
    """Central transport using this bench's pilot token."""

    TOKEN_HEADER = "X-Pilot-Token"

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def heartbeat(self) -> dict[str, Any]:
        """Verify Central auth and return its identity echo."""
        return self._get("/api/method/central.api.pilot.heartbeat")

    def forward(self, method_path: str, http_method: str, data: dict[str, Any] | None = None) -> Any:
        """Proxy an arbitrary Central pilot-API method with the X-Pilot-Token, returning its
        result (the ``{"message": ...}`` envelope unwrapped). The caller decides what's reachable."""
        return _message(self._request(f"/api/method/{method_path}", method=http_method, data=data))

    def _credentials(self) -> tuple[str, str]:
        endpoint, token = self._bench_toml_credentials()
        if not (endpoint and token):
            endpoint, token = self._legacy_common_site_config_credentials()
        if not endpoint or not token:
            raise CentralClientError("central.endpoint / central.auth_token not set in bench.toml")
        return endpoint.rstrip("/"), token

    def _bench_toml_credentials(self) -> tuple[str | None, str | None]:
        central = self.bench.config.central
        return central.endpoint, central.auth_token

    def _legacy_common_site_config_credentials(self) -> tuple[str | None, str | None]:
        path = self.bench.sites_path / "common_site_config.json"
        try:
            config = json.loads(path.read_text())
        except (FileNotFoundError, ValueError):
            return None, None
        return config.get("central_endpoint"), config.get("central_auth_token")

    def _get(self, path: str) -> dict[str, Any]:
        return self._request(path, method="GET")

    def _post(self, path: str, data: dict[str, Any]) -> dict[str, Any]:
        return self._request(path, method="POST", data=data)

    def _request(self, path: str, method: str, data: dict[str, Any] | None = None) -> dict[str, Any]:
        endpoint, token = self._credentials()
        headers = {self.TOKEN_HEADER: token}
        body = None
        if data is not None:
            headers["Content-Type"] = "application/json"
            body = json.dumps(data).encode()
        request = urllib.request.Request(f"{endpoint}{path}", data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise CentralClientError(f"Central returned HTTP {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            raise CentralClientError(f"Cannot reach Central at {endpoint}: {exc.reason}") from exc
        except ValueError as exc:
            raise CentralClientError(f"Central returned a non-JSON response for {path}: {exc}") from exc
