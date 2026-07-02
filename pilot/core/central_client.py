from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class CentralClientError(Exception):
    """A Central API call could not be made or was rejected (missing config,
    transport failure, or a non-2xx response)."""


class CentralClient:
    """Calls Central's HTTP API on behalf of this bench's pilot.

    Reads ``central.endpoint`` + ``central.auth_token`` from ``bench.toml`` (written
    by ``bench set-central-config`` at deploy) and authenticates with the
    ``X-Pilot-Token`` header — the reverse of the
    site→bench ``pilot_auth_token`` (PR #133).
    """

    TOKEN_HEADER = "X-Pilot-Token"

    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def heartbeat(self) -> dict[str, Any]:
        """Prove this pilot can authenticate to Central; returns Central's identity echo
        (team + pilot_credential_id)."""
        return self._get("/api/method/central.api.pilot.heartbeat")

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
        endpoint, token = self._credentials()
        request = urllib.request.Request(f"{endpoint}{path}", method="GET", headers={self.TOKEN_HEADER: token})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            raise CentralClientError(f"Central returned HTTP {exc.code} for {path}") from exc
        except urllib.error.URLError as exc:
            raise CentralClientError(f"Cannot reach Central at {endpoint}: {exc.reason}") from exc
        except ValueError as exc:
            # A 2xx with a non-JSON body (e.g. an HTML error page from a proxy) — decode /
            # json.loads raise ValueError, which the urllib guards above don't cover.
            raise CentralClientError(f"Central returned a non-JSON response for {path}: {exc}") from exc
