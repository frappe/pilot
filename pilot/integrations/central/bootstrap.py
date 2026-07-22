from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from pilot.integrations.central.client import CentralClientError, _message

if TYPE_CHECKING:
    from pilot.core.bench import Bench

ENROLL_METHOD = "central.api.pilot.enroll"

DEFAULT_SEED_PATH = "/run/pilot/central-seed.json"


def default_seed_path() -> str:
    import os

    return os.environ.get("PILOT_SEED_PATH", DEFAULT_SEED_PATH)


def seed_from_metadata(bench: "Bench", path: str) -> bool:
    from pathlib import Path

    source = Path(path)
    if not source.exists():
        return False
    data = json.loads(source.read_text())
    endpoint, token = data.get("central_endpoint"), data.get("bootstrap_token")
    if not endpoint or not token:
        return False
    seed(bench, endpoint, token)
    return True


def seed(bench: "Bench", endpoint: str, bootstrap_token: str) -> None:
    from pilot.config.bench import BenchConfig

    config = BenchConfig.read_raw(bench.path)
    central = config.setdefault("central", {})
    central["endpoint"] = endpoint
    central["bootstrap_token"] = bootstrap_token
    BenchConfig.write_raw(bench.path, config)

    bench.config.central.endpoint = endpoint
    bench.config.central.bootstrap_token = bootstrap_token


def enroll_if_needed(bench: "Bench") -> bool:
    central = bench.config.central
    if central.auth_token:
        return False
    if not central.endpoint or not central.bootstrap_token:
        raise CentralClientError(
            "Cannot enrol: central.endpoint / central.bootstrap_token not set in bench.toml"
        )
    result = _enroll(central.endpoint.rstrip("/"), central.bootstrap_token)
    _persist(bench, result)
    return True


def _enroll(endpoint: str, bootstrap_token: str) -> dict[str, Any]:
    body = json.dumps({"bootstrap_token": bootstrap_token}).encode()
    request = urllib.request.Request(
        f"{endpoint}/api/method/{ENROLL_METHOD}",
        data=body,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            payload = json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        raise CentralClientError(f"Enrollment rejected: Central returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        raise CentralClientError(f"Cannot reach Central at {endpoint}: {exc.reason}") from exc
    except ValueError as exc:
        raise CentralClientError(f"Central returned a non-JSON enrollment response: {exc}") from exc
    return _message(payload)


def _persist(bench: "Bench", result: dict[str, Any]) -> None:
    from pilot.config.bench import BenchConfig

    missing = [key for key in ("auth_token", "jwks_url", "audience_id") if not result.get(key)]
    if missing:
        raise CentralClientError(f"Enrollment response missing: {', '.join(missing)}")

    config = BenchConfig.read_raw(bench.path)

    central = config.setdefault("central", {})
    central["endpoint"] = result.get("central_endpoint") or central.get("endpoint", "")
    central["auth_token"] = result["auth_token"]
    central.pop("bootstrap_token", None)

    admin = config.setdefault("admin", {})
    admin["jwks_url"] = result["jwks_url"]
    admin["jwks_audience"] = result["audience_id"]

    BenchConfig.write_raw(bench.path, config)

    bench.config.central.endpoint = central["endpoint"]
    bench.config.central.auth_token = central["auth_token"]
    bench.config.central.bootstrap_token = ""
    bench.config.admin.jwks_url = result["jwks_url"]
    bench.config.admin.jwks_audience = result["audience_id"]
