from __future__ import annotations

import json
import math
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from pilot.config import BenchConfig
    from pilot.core.bench.diagnostics import DiagnosticReport

PROBES = {
    "database": (
        "database_connectivity",
        "Execute a read-only database query using this site's credentials.",
        "Check the site's database configuration, credentials, database file, and server availability.",
    ),
    "redis": (
        "redis_ping",
        "Authenticate when configured and receive Redis PONG.",
        "Check the Redis URL, credentials, and whether the configured Redis service is running.",
    ),
    "web": (
        "web_health",
        "Receive Frappe JSON pong from the site's local HTTP backend.",
        "Start the bench workload and check its web logs, site routing, and database configuration.",
    ),
}


class RuntimeDiagnostics:
    """Protocol probes isolated behind a whole-process deadline; secrets travel only through stdin."""

    def __init__(self, report: DiagnosticReport, offline: bool, timeout: float) -> None:
        if not math.isfinite(timeout) or not 0.1 <= timeout <= 30:
            raise ValueError("Probe timeout must be between 0.1 and 30 seconds.")
        self.report = report
        self.offline = offline
        self.timeout = timeout

    def run(self, config: BenchConfig | None, common: dict, sites: dict[str, dict]) -> None:
        if config is None:
            self.add(
                "runtime",
                "skipped",
                "bench",
                self.report.path,
                "configuration_invalid",
                "Runtime checks require valid bench configuration.",
                "Repair bench configuration and retry.",
            )
            return
        self.check_processes()
        for role, port in (("cache", config.redis.cache_port), ("queue", config.redis.queue_port)):
            url = common.get(f"redis_{role}", f"redis://127.0.0.1:{port}")
            self.probe("redis", "service", f"redis_{role}", {"url": url})
        if not sites:
            self.add(
                "site_runtime",
                "skipped",
                "bench",
                self.report.path,
                "no_valid_sites",
                "No valid site configuration is available for database and HTTP checks.",
                "",
            )
        for site, values in sites.items():
            self.check_database(config, site, values)
            self.probe("web", "site", site, {"port": config.http_port, "site": site})

    def check_processes(self) -> None:
        if self.offline:
            for code in ("worker_process", "scheduler_process"):
                self.add(
                    code,
                    "skipped",
                    "bench",
                    self.report.path,
                    "offline",
                    "Runtime probe disabled by --offline.",
                    "",
                )
            return
        result = self.execute("processes", {"path": self.report.path})
        if result["status"] != "ok":
            self.add(
                "worker_process",
                result["status"],
                "bench",
                self.report.path,
                result["reason"],
                "Cannot inspect worker and scheduler process states.",
                "Check process manager availability and retry.",
            )
            return
        for row in result.get("checks", []):
            self.add(
                row["code"],
                row["status"],
                "service",
                row["target"],
                row["reason"],
                "Process state observed; execution and heartbeat health are not verified.",
                "Check bench process status and logs; use pilot start or the configured manager to restore the workload."
                if row["status"] in ("error", "unavailable")
                else "",
                details=row["details"],
            )

    def check_database(self, config: BenchConfig, site: str, values: dict) -> None:
        try:
            settings = self.database_settings(config, site, values)
        except (KeyError, ValueError, TypeError):
            self.add(
                "database_connectivity",
                "error",
                "site",
                site,
                "configuration_invalid",
                "Database credentials or settings are incomplete.",
                PROBES["database"][2],
            )
            return
        self.probe("database", "site", site, settings)

    def database_settings(self, config: BenchConfig, site: str, values: dict) -> dict:
        engine = values.get("db_type", "mariadb")
        name = values["db_name"]
        if not isinstance(name, str) or not name or engine != config.db_type:
            raise ValueError
        if engine == "sqlite":
            if Path(name).name != name or name in (".", ".."):
                raise ValueError
            path = Path(self.report.path) / "sites" / site / "db" / f"{name}.db"
            return {"db_type": engine, "path": str(path)}
        defaults = config.postgres if engine == "postgres" else config.mariadb
        return {
            "db_type": engine,
            "host": values.get("db_host", defaults.host),
            "port": int(values.get("db_port", defaults.port)),
            "socket": values.get("db_socket") or getattr(defaults, "socket_path", None),
            "user": values.get("db_user") or name,
            "password": values["db_password"],
            "database": name,
        }

    def probe(self, kind: str, scope: str, target: str, settings: dict) -> None:
        code, description, fix = PROBES[kind]
        if self.offline:
            self.add(code, "skipped", scope, target, "offline", "Runtime probe disabled by --offline.", "")
            return
        started = time.monotonic()
        result = self.execute(kind, settings)
        status, reason = result["status"], result["reason"]
        if status == "unavailable":
            fix = "Install Pilot's admin dependencies in the Python environment running doctor, then retry."
        message = description if status == "ok" else "Runtime probe could not establish protocol health."
        self.add(
            code,
            status,
            scope,
            target,
            reason,
            message,
            "" if status == "ok" else fix,
            round((time.monotonic() - started) * 1000),
        )

    def execute(self, kind: str, settings: dict) -> dict:
        try:
            result = subprocess.run(
                [sys.executable, "-m", "pilot.core.bench.diagnostic_probe"],
                input=json.dumps({"kind": kind, "settings": settings}),
                text=True,
                capture_output=True,
                timeout=self.timeout,
                check=False,
                cwd=Path(__file__).resolve().parents[3],
                env={
                    **os.environ,
                    "PYTHONPATH": os.pathsep.join(
                        [str(Path(__file__).resolve().parents[3]), os.environ.get("PYTHONPATH", "")]
                    ),
                },
            )
            value = json.loads(result.stdout)
            if (
                result.returncode
                or not isinstance(value, dict)
                or value.get("status") not in ("ok", "error", "unavailable")
            ):
                raise ValueError
            return value
        except subprocess.TimeoutExpired:
            return {"status": "error", "reason": "timeout"}
        except (OSError, ValueError):
            return {"status": "unavailable", "reason": "probe_unavailable"}

    def add(
        self,
        code: str,
        status: Literal["ok", "error", "skipped", "unavailable"],
        scope: str,
        target: str,
        reason: str,
        message: str,
        fix: str,
        duration_ms: int | None = None,
        details: dict | None = None,
    ) -> None:
        from pilot.core.bench.diagnostics import DiagnosticCheck

        self.report.checks.append(
            DiagnosticCheck(
                code=code,
                status=status,
                path=self.report.path,
                message=message,
                fix=fix,
                scope=scope,
                target=target,
                reason=reason,
                duration_ms=duration_ms,
                details=details or {},
            )
        )
