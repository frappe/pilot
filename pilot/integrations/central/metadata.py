from __future__ import annotations

import json
import os
import urllib.request
from typing import TYPE_CHECKING, Any

from pilot.integrations.central.client import CentralClientError

if TYPE_CHECKING:
    from collections.abc import Callable

    from pilot.config.bench import BenchConfig
    from pilot.core.bench import Bench

METADATA_BASE = "http://169.254.169.254/latest"
TOKEN_TTL_SECONDS = 21600
REQUIRED_KEYS = ("central_endpoint", "central_auth_token", "jwks_url", "jwks_audience_id")


def attribute_name() -> str:
    return os.environ.get("PILOT_METADATA_KEY", "pilot-central")


def _parse_credentials(raw: str, name: str) -> dict[str, Any]:
    """Anything rejected here is a provisioning bug, not a host still waiting."""
    from pilot.internal.validators import validate_external_url

    source = f"Instance metadata '{name}'"
    try:
        payload = json.loads(raw)
    except ValueError as exc:
        raise CentralClientError(f"{source} is not JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise CentralClientError(f"{source} is not a JSON object.")

    if missing := [key for key in REQUIRED_KEYS if not payload.get(key)]:
        raise CentralClientError(f"{source} is missing: {', '.join(missing)}")

    credentials: dict[str, Any] = {key: str(payload[key]) for key in REQUIRED_KEYS}
    if error := validate_external_url(credentials["central_endpoint"], "central_endpoint"):
        raise CentralClientError(f"{source}: {error}")

    initial_jwks_cache = payload.get("initial_jwks_cache")
    if initial_jwks_cache is not None:
        if not isinstance(initial_jwks_cache, dict):
            raise CentralClientError(f"{source}: initial_jwks_cache is not a JSON object.")
        credentials["initial_jwks_cache"] = initial_jwks_cache

    return credentials


class InstanceMetadata:
    """The cloud metadata service, which owns this host's Central credential."""

    def __init__(self, base_url: str = METADATA_BASE, timeout: float = 2.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def get_credentials(self) -> dict[str, Any] | None:
        """The staged credential, or None until the cloud writes it. Malformed raises."""
        name = attribute_name()
        raw = self.get_attribute(name)
        return _parse_credentials(raw, name) if raw else None

    def get_attribute(self, name: str) -> str | None:
        token = self._token()
        if not token:
            return None

        return self._read(
            f"{self.base_url}/meta-data/attributes/{name}",
            {"X-metadata-token": token},
        )

    def _token(self) -> str | None:
        return self._read(
            f"{self.base_url}/api/token",
            {"X-metadata-token-ttl-seconds": str(TOKEN_TTL_SECONDS)},
            method="PUT",
        )

    def _read(self, url: str, headers: dict[str, str], method: str = "GET") -> str | None:
        request = urllib.request.Request(url, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return response.read().decode().strip() or None
        except (OSError, ValueError):
            return None


def apply_central_config(
    bench: "Bench",
    metadata: InstanceMetadata | None = None,
    on_credentials: Callable[[dict[str, Any]], None] | None = None,
) -> bool:
    """Mark this host bootstrapped and save its JWKS issuer. False means retry later.

    ``on_credentials`` runs before the host reads as bootstrapped, so work the first
    remote token needs, such as storing the issuer's keys, is already done.
    """
    if not bench.config.central.is_awaiting_bootstrap:
        return False

    credentials = (metadata or InstanceMetadata()).get_credentials()
    if credentials is None:
        return False

    if on_credentials is not None:
        on_credentials(credentials)

    from pilot.config.common import CommonConfig

    # Every field written here is host-shared, so the whole write goes through
    # the shared file's own lock. Going via BenchConfig would read that file
    # before locking it and write the snapshot back, losing a concurrent edit.
    with CommonConfig.open(bench.path.parent) as common:
        if not common.central.is_awaiting_bootstrap:
            return False  # another process got there first
        common.central.bootstrapped = True
        common.jwks_url = credentials["jwks_url"]
        common.jwks_audience = credentials["jwks_audience_id"]

    _mark_bootstrapped(bench.config, credentials)
    return True


def _mark_bootstrapped(config: "BenchConfig", credentials: dict[str, Any]) -> None:
    """Bring the config already in memory up to date with what was just saved."""
    config.central.bootstrapped = True
    config.admin.jwks_url = credentials["jwks_url"]
    config.admin.jwks_audience = credentials["jwks_audience_id"]
