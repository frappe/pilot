from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import TYPE_CHECKING, Any

from pilot.core.central_client import CentralClientError, _message

if TYPE_CHECKING:
    from pilot.core.bench import Bench

# First-boot enrollment: the bench exchanges a single-use bootstrap token (seeded by Atlas
# at VM-create) for its long-lived credential + JWKS config, in one call.

ENROLL_METHOD = "central.api.pilot.enroll"

# Canonical path a bare `bench enroll` reads the seed from (VM metadata drops it here; on
# tmpfs, so the single-use token doesn't survive a reboot). Override with $PILOT_SEED_PATH.
DEFAULT_SEED_PATH = "/run/pilot/central-seed.json"


def default_seed_path() -> str:
    import os

    return os.environ.get("PILOT_SEED_PATH", DEFAULT_SEED_PATH)


def seed_from_metadata(bench: "Bench", path: str) -> bool:
    """Stage a seed (``{central_endpoint, bootstrap_token}`` JSON) that VM metadata dropped
    at ``path``. Returns True if one was found — the boot-free enrollment entry point."""
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
    """Write the create-time seed into bench.toml ``[central]`` + the in-memory config, so
    ``enroll_if_needed`` can exchange it."""
    from pilot.config.toml_store import BenchTomlStore

    store = BenchTomlStore.for_bench(bench.path)
    config = store.read_raw()
    central = config.setdefault("central", {})
    central["endpoint"] = endpoint
    central["bootstrap_token"] = bootstrap_token
    store.write_raw(config)

    bench.config.central.endpoint = endpoint
    bench.config.central.bootstrap_token = bootstrap_token


def enroll_if_needed(bench: "Bench") -> bool:
    """Idempotent: no-op if already enrolled, else exchange the seeded bootstrap token for
    the credential + JWKS config and persist both into bench.toml. Returns True if enrolled."""
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
    """POST the bootstrap token to Central's guest enroll endpoint. No credential is sent —
    the signed, single-use bootstrap token is the only authentication."""
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
    """Write the enrolled credential + JWKS trust config into bench.toml and refresh the
    in-memory config. The bootstrap token is dropped — it is single-use and now spent."""
    from pilot.config.toml_store import BenchTomlStore

    missing = [key for key in ("auth_token", "jwks_url", "audience_id") if not result.get(key)]
    if missing:
        raise CentralClientError(f"Enrollment response missing: {', '.join(missing)}")

    store = BenchTomlStore.for_bench(bench.path)
    config = store.read_raw()

    central = config.setdefault("central", {})
    central["endpoint"] = result.get("central_endpoint") or central.get("endpoint", "")
    central["auth_token"] = result["auth_token"]
    central.pop("bootstrap_token", None)

    admin = config.setdefault("admin", {})
    admin["jwks_url"] = result["jwks_url"]
    admin["jwks_audience"] = result["audience_id"]

    store.write_raw(config)

    bench.config.central.endpoint = central["endpoint"]
    bench.config.central.auth_token = central["auth_token"]
    bench.config.central.bootstrap_token = ""
    bench.config.admin.jwks_url = result["jwks_url"]
    bench.config.admin.jwks_audience = result["audience_id"]
