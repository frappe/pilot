from __future__ import annotations

import tomllib
from pathlib import Path

from bench_cli.commands.base import Command


class ListCommand(Command):
    name = "ls"
    help = "List all benches."
    requires_bench = False

    def __init__(self, bench=None) -> None:
        self.bench = bench

    def run(self) -> None:
        from bench_cli.loader import cli_root

        benches_dir = cli_root() / "benches"
        rows = self._collect(benches_dir)
        if not rows:
            print("No benches yet. Create one with: bench new <name>")
            return

        # Column widths sized to content (with sensible minimums).
        name_w = max(len("NAME"), *(len(r["name"]) for r in rows))
        mode_w = max(len("MODE"), *(len(r["mode"]) for r in rows))
        mgr_w = max(len("MANAGER"), *(len(r["manager"]) for r in rows))
        sites_w = max(len("SITES"), *(len(str(r["sites"])) for r in rows))

        header = (
            f"  {'':1} {'NAME':<{name_w}}  {'MODE':<{mode_w}}  "
            f"{'MANAGER':<{mgr_w}}  {'SITES':<{sites_w}}  ADDRESS"
        )
        print(_dim(header))
        for r in rows:
            dot = {"running": _ok("●"), "admin": _warn("●")}.get(r["state"], _dim("○"))
            print(
                f"  {dot} {r['name']:<{name_w}}  {r['mode']:<{mode_w}}  "
                f"{r['manager']:<{mgr_w}}  {str(r['sites']):<{sites_w}}  {r['address']}"
            )

    def _collect(self, benches_dir: Path) -> list[dict]:
        if not benches_dir.is_dir():
            return []
        rows = []
        for bench_dir in sorted(benches_dir.iterdir()):
            toml_path = bench_dir / "bench.toml"
            if not bench_dir.is_dir() or not toml_path.exists():
                continue
            rows.append(self._describe(bench_dir, toml_path))
        return rows

    def _describe(self, bench_dir: Path, toml_path: Path) -> dict:
        from bench_cli.config.bench_config import BenchConfig
        from bench_cli.core.bench import Bench

        name = bench_dir.name
        mode, manager, address, state, sites = "unknown", "-", "", "stopped", 0
        try:
            # Parse-only (no validate) so a half-configured bench still lists.
            config = BenchConfig._from_dict(tomllib.loads(toml_path.read_text()))
            name = config.name or name
            prod = config.production
            if prod.enabled:
                mode = "production"
                manager = prod.process_manager or "-"
            else:
                mode = "development"
                manager = "foreground"
            from bench_cli.admin_url import admin_url

            address = admin_url(config)
            state = self._state(Bench(config, bench_dir), prod.enabled)
            sites = self._site_count(bench_dir)
        except Exception:
            pass
        return {"name": name, "mode": mode, "manager": manager, "address": address, "state": state, "sites": sites}

    def _site_count(self, bench_dir: Path) -> int:
        """A sites/ subdir counts as a site iff it has a site_config.json."""
        sites_dir = bench_dir / "sites"
        if not sites_dir.is_dir():
            return 0
        return sum(1 for d in sites_dir.iterdir() if d.is_dir() and (d / "site_config.json").exists())

    def _state(self, bench, production: bool) -> str:
        """Match the admin UI's view: 'running' when the workload is up, 'admin'
        when only the (socket-activated) admin control plane is up — e.g. a bench
        provisioned but not yet set up — and 'stopped' otherwise. A dev bench is
        'running' iff its foreground admin is reachable."""
        from bench_cli.managers.process_manager import ProcessManager

        try:
            if not production:
                manager = ProcessManager.detect_running(bench)
                return "running" if manager.admin_is_running() else "stopped"
            manager = ProcessManager.for_bench(bench)
            if manager.is_running():
                return "running"
            return "admin" if manager.admin_is_running() else "stopped"
        except Exception:
            return "stopped"


def _ok(text: str) -> str:
    return f"\033[32m{text}\033[0m"


def _warn(text: str) -> str:
    return f"\033[33m{text}\033[0m"


def _dim(text: str) -> str:
    return f"\033[90m{text}\033[0m"
