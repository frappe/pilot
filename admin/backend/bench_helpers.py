from __future__ import annotations

import http.client
import socket
from pathlib import Path

from pilot.config.bench_config import BenchConfig
from pilot.config.toml_store import BenchTomlStore


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def workload_running(bench_dir: Path, toml_path: Path) -> bool | None:
    """Whether a production bench's workload is currently running, or None if unknown."""
    from pilot.core.bench import Bench
    from pilot.managers.process_manager import ProcessManager

    try:
        bench = Bench(BenchTomlStore(toml_path).read(), bench_dir)
        return ProcessManager.for_bench(bench).is_running()
    except Exception:
        return None


def admin_running(bench_dir: Path, toml_path: Path) -> bool | None:
    """Whether a production bench's admin control plane is up, or None if unknown."""
    from pilot.core.bench import Bench
    from pilot.managers.process_manager import ProcessManager

    try:
        bench = Bench(BenchConfig.from_file(toml_path), bench_dir)
        return ProcessManager.for_bench(bench).is_admin_running()
    except Exception:
        return None


def admin_cert_exists(bench_dir: Path, toml_path: Path) -> bool:
    """Whether the admin domain's TLS cert is in place."""
    from pilot.core.bench import Bench
    from pilot.managers.nginx_manager import NginxManager

    try:
        bench = Bench(BenchTomlStore(toml_path).read(), bench_dir)
        return NginxManager(bench).admin_cert_exists()
    except Exception:
        return False


def site_count(bench_dir: Path) -> int:
    """Number of sites in a bench."""
    sites_dir = bench_dir / "sites"
    if not sites_dir.is_dir():
        return 0
    return sum(1 for d in sites_dir.iterdir() if d.is_dir() and (d / "site_config.json").exists())


def persist_toml(bench_dir: Path, updates: dict) -> None:
    """Merge ``updates`` into a bench's bench.toml in place."""
    store = BenchTomlStore.for_bench(bench_dir)
    data = store.read_raw()
    for section, values in updates.items():
        data.setdefault(section, {}).update(values)
    store.write_raw(data)


def nginx_http_port(bench_root: Path) -> int:
    try:
        return int(BenchTomlStore.for_bench(bench_root).read_raw().get("nginx", {}).get("http_port", 80))
    except Exception:
        return 80


def wizard_responds(bench_root: Path, domain: str, scheme: str = "http") -> bool:
    """Whether a production bench's wizard answers at its admin domain."""
    conn = http.client.HTTPConnection("127.0.0.1", nginx_http_port(bench_root), timeout=3)
    try:
        conn.request("GET", "/api/ping", headers={"Host": domain})
        status = conn.getresponse().status
        return status == 200 or (scheme == "https" and status in (301, 308))
    except OSError:
        return False
    finally:
        conn.close()


def current_is_production(bench_root: Path) -> bool:
    try:
        prod = BenchTomlStore.for_bench(bench_root).read_raw().get("production", {})
        pm = str(prod.get("process_manager", "")).lower()
        return bool(prod.get("enabled", pm not in ("", "none")))
    except Exception:
        return False
