from __future__ import annotations

import argparse
import logging
import threading
import time
from pathlib import Path

from flask import Flask

_POLL_SECONDS = 0.1


class CentralBootstrapWatcher:
    """Poll instance metadata and apply the Central credential when available."""

    def __init__(self, bench_root: Path, interval: float = _POLL_SECONDS) -> None:
        self.bench_root = bench_root
        self.interval = interval

    def install(self, app: Flask) -> None:
        threading.Thread(
            target=self._watch,
            name="central-bootstrap-watcher",
            daemon=True,
        ).start()

    def check_once(self) -> bool:
        """True once the credential has been written to this host's config.

        Nothing here is fatal: the credential, and any config it trips over, can
        still be fixed in place, so a failure is reported and retried rather than
        ending the watch.
        """
        from pilot.config import BenchConfig
        from pilot.core.bench import Bench
        from pilot.integrations.central import apply_central_config

        try:
            config = BenchConfig.read(self.bench_root, validate=False)
            return apply_central_config(Bench(config, self.bench_root), on_credentials=self.seed_jwks_cache)
        except Exception:
            logging.exception("Central bootstrap attempt failed")
            return False

    def seed_jwks_cache(self, credentials: dict) -> None:
        """Store the issuer's keys delivered with the credential, so the first token needs no fetch."""
        from admin.backend.internal.jwks_cache import JwksCache

        initial_jwks_cache = credentials.get("initial_jwks_cache")
        if initial_jwks_cache is not None:
            JwksCache(self.bench_root.parent, credentials["jwks_url"]).seed(initial_jwks_cache)

    def run_until_applied(self) -> None:
        """Retry until the Central credential is applied."""
        while not self.check_once():
            time.sleep(self.interval)

    def _watch(self) -> None:
        self.run_until_applied()
        logging.info("Central bootstrap applied; this host is configured.")


def install_central_bootstrap_watcher(app: Flask, bench_root: Path) -> CentralBootstrapWatcher | None:
    """Start the watcher only on a host still awaiting its credential."""
    from pilot.config import BenchConfig

    try:
        config = BenchConfig.read(bench_root, validate=False)
    except Exception:
        logging.exception("Cannot tell whether this host is awaiting a Central credential")
        return None

    if not config.central.is_awaiting_bootstrap:
        return None

    existing = app.extensions.get("central_bootstrap_watcher")
    if existing is not None:
        return existing

    watcher = CentralBootstrapWatcher(bench_root)
    app.extensions["central_bootstrap_watcher"] = watcher
    watcher.install(app)
    return watcher


def main() -> None:
    """Apply the Central credential at boot."""
    parser = argparse.ArgumentParser(description="Apply this host's Central credential.")
    parser.add_argument("--bench-root", required=True, type=Path, help="Path of the bench directory.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    from pilot.config import BenchConfig

    config = BenchConfig.read(args.bench_root, validate=False)
    if not config.central.is_awaiting_bootstrap:
        logging.info("This host is not awaiting a Central credential.")
        return

    CentralBootstrapWatcher(args.bench_root).run_until_applied()
    logging.info("Central bootstrap applied; this host is configured.")


if __name__ == "__main__":
    main()
