from __future__ import annotations

import typing
from collections.abc import Callable

from pilot.core.credential import RotatingCredential

if typing.TYPE_CHECKING:
    from pilot.core.bench import Bench


class DatumCredential(RotatingCredential):
    """The Datum token metrics and logs ship with, rotated from Central."""

    def __init__(
        self,
        bench: Bench,
        token: str,
        on_progress: Callable[[str], None] = lambda message: None,
    ) -> None:
        super().__init__(token)
        self.bench = bench
        self.on_progress = on_progress

    def rotate(self) -> str:
        """Issue a new telemetry token and return it."""
        apply_credential(self.bench, on_progress=self.on_progress)
        return self.bench.config.telemetry.token


def apply_credential(
    bench: Bench,
    *,
    endpoint: str | None = None,
    token: str | None = None,
    on_progress: Callable[[str], None] = lambda message: None,
) -> None:
    """Write this bench's Datum credential: what was passed, else what Central hands out.

    Metrics and logs present the same one, so it is fetched once and both read it. Central
    is the authority and the token expires, so a run takes what Central says now rather
    than keeping a stale copy. A region Central cannot name a Datum for leaves the config
    untouched, and neither shipper starts."""
    from pilot.config import BenchConfig
    from pilot.integrations.central import CentralClient
    from pilot.integrations.central.client import CentralClientError

    if not (endpoint and token) and bench.config.central.enabled:
        try:
            fetched = CentralClient().datum_token()
        except CentralClientError as exc:
            on_progress(f"Could not fetch a Datum token from Central: {exc}")
            fetched = {}
        endpoint = endpoint or fetched.get("endpoint")
        token = token or fetched.get("token")

    if not (endpoint or token):
        return

    with BenchConfig.open(bench.path) as config:
        if endpoint:
            config.telemetry.endpoint = endpoint
        if token:
            config.telemetry.token = token

    if endpoint:
        bench.config.telemetry.endpoint = endpoint
    if token:
        bench.config.telemetry.token = token
