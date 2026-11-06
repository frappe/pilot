from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Literal

from pilot.config import AppConfig, BenchConfig
from pilot.exceptions import BenchError


@dataclass(frozen=True)
class DiagnosticCheck:
    code: str
    status: Literal["ok", "error"]
    path: str
    message: str
    fix: str = ""


@dataclass
class DiagnosticReport:
    path: str
    checks: list[DiagnosticCheck] = field(default_factory=list)

    @property
    def has_errors(self) -> bool:
        return any(check.status == "error" for check in self.checks)

    def to_dict(self) -> dict:
        return {
            "schema_version": 1,
            "path": self.path,
            "ok": not self.has_errors,
            "checks": [asdict(check) for check in self.checks],
        }


class BenchDiagnostics:
    """Read-only filesystem and configuration checks, independent of bench initialization."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path).expanduser().absolute()
        self.report = DiagnosticReport(str(self.path))

    def run(self) -> DiagnosticReport:
        self.report = DiagnosticReport(str(self.path))
        config = self.check_config()
        self.run_check("environment", self.check_environment)
        common = self.check_json(self.path / "sites" / "common_site_config.json", "common_config")
        if config is not None:
            self.run_check("apps", lambda: self.check_apps(config))
        self.run_check("sites", lambda: self.check_sites(config, common or {}))
        return self.report

    def run_check(self, code: str, check: Callable[[], None]) -> None:
        try:
            check()
        except (OSError, ValueError, RuntimeError):
            self.record(
                code,
                self.path,
                False,
                "Cannot inspect these files.",
                "Check directory permissions, file encoding, and symlinks, then retry.",
            )

    def record(self, code: str, path: Path, passed: bool, message: str, fix: str = "") -> None:
        self.report.checks.append(
            DiagnosticCheck(code, "ok" if passed else "error", str(path), message, "" if passed else fix)
        )

    def check_config(self) -> BenchConfig | None:
        path = self.path / "bench.toml"
        try:
            config = BenchConfig.read(self.path, strict=True)
        except (OSError, ValueError, TypeError, KeyError, BenchError):
            self.record(
                "bench_config",
                path,
                False,
                "Bench configuration is missing, unreadable, or invalid.",
                "Repair bench.toml and the sibling common_config.toml; see docs/configuration.md.",
            )
            return None
        self.record("bench_config", path, True, "Bench configuration is valid.")
        return config

    def check_environment(self) -> None:
        python = self.path / "env" / "bin" / "python"
        ready = python.is_file() and os.access(python, os.X_OK)
        self.record(
            "python",
            python,
            ready,
            "Bench Python is executable." if ready else "Bench Python is missing or not executable.",
            "Run pilot init from this bench to create or repair its environment.",
        )
        marker = self.path / ".initializing"
        initializing = marker.exists()
        self.record(
            "initialization",
            marker,
            not initializing,
            "Initialization is incomplete." if initializing else "No interrupted initialization marker.",
            "Retry pilot init; do not delete the marker to bypass initialization.",
        )

    def check_apps(self, config: BenchConfig) -> None:
        from pilot.core.app import App
        from pilot.core.bench import Bench

        registered = self.read_registered_apps()
        bench = Bench(config, self.path)
        for app in config.apps:
            root = self.path / "apps" / app.name
            module = App(app, bench).module_name
            if not root.is_dir() and (self.path / "apps" / module).is_dir():
                root = self.path / "apps" / module
            self.check_app(app, root, module)
            if registered is not None:
                present = module in registered
                self.record(
                    "app_registration",
                    root,
                    present,
                    f"App {app.name} is registered."
                    if present
                    else f"App {app.name} is absent from apps.txt.",
                    "Run pilot init to finish app installation and registration.",
                )

    def check_app(self, app: AppConfig, root: Path, module: str) -> None:
        ready = root.is_dir() and (root / module / "hooks.py").is_file() and (root / ".git").exists()
        self.record(
            "app_checkout",
            root,
            ready,
            f"App {app.name} checkout is present."
            if ready
            else f"App {app.name} checkout is missing or incomplete.",
            "Restore the app checkout or retry pilot init; for linked apps, restore the source repository.",
        )
        if app.is_linked:
            matches = (
                root.is_symlink()
                and root.is_dir()
                and root.resolve() == Path(app.path).expanduser().resolve()
            )
            self.record(
                "app_link",
                root,
                matches,
                f"App {app.name} links to its configured checkout."
                if matches
                else f"App {app.name} link is broken or points elsewhere.",
                "Restore the configured source path and rerun pilot dev setup from that repository.",
            )

    def read_registered_apps(self) -> set[str] | None:
        path = self.path / "sites" / "apps.txt"
        try:
            names = set(path.read_text(encoding="utf-8").splitlines())
        except (OSError, UnicodeError):
            self.record(
                "apps_list",
                path,
                False,
                "App registration file is missing or unreadable.",
                "Run pilot init to generate sites/apps.txt.",
            )
            return None
        self.record("apps_list", path, True, "App registration file is readable.")
        return names

    def check_json(self, path: Path, code: str) -> dict | None:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.record(
                code,
                path,
                False,
                "Configuration is missing, unreadable, or invalid JSON.",
                "Restore or repair this JSON file; it must contain an object.",
            )
            return None
        if not isinstance(value, dict):
            self.record(
                code,
                path,
                False,
                "Configuration must contain a JSON object.",
                "Restore or repair this JSON file.",
            )
            return None
        self.record(code, path, True, "Configuration is a readable JSON object.")
        return value

    def check_sites(self, config: BenchConfig | None, common: dict) -> None:
        sites = self.path / "sites"
        if not sites.is_dir():
            self.record("sites_directory", sites, False, "Sites directory is missing.", "Run pilot init.")
            return
        for site in sorted(sites.iterdir()):
            if site.is_symlink() or not site.is_dir() or not (site / "site_config.json").exists():
                continue
            values = self.check_json(site / "site_config.json", "site_config")
            if values is not None:
                self.check_site_database(site, {**common, **values}, config)

    def check_site_database(self, site: Path, values: dict, config: BenchConfig | None) -> None:
        engine = values.get("db_type", "mariadb")
        valid = isinstance(values.get("db_name"), str) and bool(values["db_name"])
        self.record(
            "site_database_name",
            site / "site_config.json",
            valid,
            "Site database name is configured." if valid else "Site database name is missing or invalid.",
            "Restore db_name from this site's original configuration; do not invent a replacement.",
        )
        if config is not None:
            matches = engine == config.db_type
            self.record(
                "site_database_engine",
                site / "site_config.json",
                matches,
                "Site database engine matches the bench."
                if matches
                else "Site database engine differs from the bench.",
                "Check db_type in bench.toml and the site's original configuration before changing either.",
            )
