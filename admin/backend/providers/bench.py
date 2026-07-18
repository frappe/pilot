from __future__ import annotations

import http.client
import socket
from pathlib import Path

from pilot.config import BenchConfig


class BenchProvider:
    """Read-only status checks against one bench's directory and bench.toml."""

    def __init__(self, bench_dir: Path) -> None:
        self._bench_dir = bench_dir
        self._toml_path = bench_dir / "bench.toml"

    @property
    def site_count(self) -> int:
        sites_dir = self._bench_dir / "sites"
        if not sites_dir.is_dir():
            return 0
        return sum(1 for d in sites_dir.iterdir() if d.is_dir() and (d / "site_config.json").exists())

    @property
    def is_production(self) -> bool:
        try:
            return BenchConfig.read(self._bench_dir, validate=False).production.enabled
        except Exception:
            return False

    @property
    def has_admin_cert(self) -> bool:
        """Whether the admin domain's TLS cert is in place."""
        from pilot.core.bench import Bench
        from pilot.managers.nginx import NginxManager

        try:
            bench = Bench(self._bench_dir)
            return NginxManager(bench).has_admin_cert
        except Exception:
            return False

    @property
    def is_workload_running(self) -> bool | None:
        """Whether a production bench's workload is currently running, or None if unknown."""
        from pilot.core.bench import Bench
        from pilot.managers.processes.local import ProcessManager

        try:
            bench = Bench(self._bench_dir)
            return ProcessManager.for_bench(bench).is_running()
        except Exception:
            return None

    @property
    def is_admin_running(self) -> bool | None:
        """Whether a production bench's admin control plane is up, or None if unknown."""
        from pilot.core.bench import Bench
        from pilot.managers.processes.local import ProcessManager

        try:
            bench = Bench(self._bench_dir)
            return ProcessManager.for_bench(bench).is_admin_running()
        except Exception:
            return None

    def is_wizard_ready(self, domain: str, scheme: str = "http") -> bool:
        """Whether a production bench's wizard answers at its admin domain."""
        try:
            port = int(BenchConfig.read_raw(self._bench_dir).get("nginx", {}).get("http_port", 80))
        except Exception:
            port = 80

        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
        try:
            conn.request("GET", "/api/v1/health", headers={"Host": domain})
            status = conn.getresponse().status
            return status == 200 or (scheme == "https" and status in (301, 308))
        except OSError:
            return False
        finally:
            conn.close()

    @staticmethod
    def is_port_open(port: int) -> bool:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            return False
