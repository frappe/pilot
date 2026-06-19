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

        header = f"  {'':1} {'NAME':<{name_w}}  {'MODE':<{mode_w}}  {'MANAGER':<{mgr_w}}  ADDRESS"
        print(_dim(header))
        for r in rows:
            dot = _ok("●") if r["running"] else _dim("○")
            print(f"  {dot} {r['name']:<{name_w}}  {r['mode']:<{mode_w}}  {r['manager']:<{mgr_w}}  {r['address']}")

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
        mode, manager, address, running = "unknown", "-", "", False
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
            running = self._is_running(Bench(config, bench_dir))
        except Exception:
            pass
        return {"name": name, "mode": mode, "manager": manager, "address": address, "running": running}

    def _is_running(self, bench) -> bool:
        """A bench counts as running if its admin (control plane) is up."""
        from bench_cli.managers.process_manager import ProcessManagerFactory

        try:
            return ProcessManagerFactory.detect_running(bench).admin_is_running()
        except Exception:
            return False


def _ok(text: str) -> str:
    return f"\033[32m{text}\033[0m"


def _dim(text: str) -> str:
    return f"\033[90m{text}\033[0m"
