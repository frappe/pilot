from __future__ import annotations

import argparse
import json
import sys
import tomllib
from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class RenameSiteCommand(Command):
    name = "rename-site"
    help = "Rename a site in this bench."

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("old_name", help="Current site name.")
        parser.add_argument("new_name", help="New site name (hostname).")

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, args.old_name, args.new_name)

    def __init__(self, bench: "Bench", old_name: str, new_name: str) -> None:
        self.bench = bench
        self.old_name = old_name
        self.new_name = new_name

    def run(self) -> None:
        old_site = self._validate()
        ssl_enabled = old_site.config.ssl

        print(f"Renaming site '{self.old_name}' -> '{self.new_name}'...")
        sys.stdout.flush()
        old_site.path.rename(self.bench.sites_path / self.new_name)

        self._update_default_site()
        self._rename_in_bench_toml()
        self._remove_stale_nginx_conf()
        self._add_to_hosts()
        self._reload_nginx()

        print(f"\nSite renamed to '{self.new_name}'.")
        self._run_followups(ssl_enabled)

    def _validate(self):
        from pilot.utils import host_owner, normalize_host

        if self.new_name == self.old_name:
            raise BenchError("New name is the same as the current name.")

        sites = {s.config.name: s for s in self.bench.sites()}
        old_site = sites.get(self.old_name)
        if old_site is None:
            raise BenchError(f"Site '{self.old_name}' does not exist in this bench.")

        if self.new_name in sites or (self.bench.sites_path / self.new_name).exists():
            raise BenchError(f"Site '{self.new_name}' already exists in this bench.")

        # Hostnames are shared across all benches' nginx, so reject one already
        # claimed by a sibling bench (as a site/alias or its admin domain).
        owner = host_owner(self.bench.path, self.new_name)
        if owner:
            raise BenchError(
                f"'{self.new_name}' is already used by bench '{owner}' (as a site or its admin domain). "
                f"All benches share one nginx, so hostnames must be unique."
            )
        if normalize_host(self.new_name) == normalize_host(self.bench.config.admin.domain):
            raise BenchError(
                f"Site '{self.new_name}' clashes with this bench's admin domain. "
                f"An admin domain must not match a site domain."
            )
        return old_site

    def _update_default_site(self) -> None:
        path = self.bench.sites_path / "common_site_config.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except Exception:
            return
        if data.get("default_site") == self.old_name:
            data["default_site"] = self.new_name
            path.write_text(json.dumps(data, indent=2) + "\n")

    def _rename_in_bench_toml(self) -> None:
        from pilot.utils import write_toml

        bench_toml = self.bench.path / "bench.toml"
        with bench_toml.open("rb") as fh:
            raw = tomllib.load(fh)
        renamed = False
        for site in raw.get("sites", []):
            if site.get("name") == self.old_name:
                site["name"] = self.new_name
                renamed = True
        if renamed:
            write_toml(bench_toml, raw)

    def _remove_stale_nginx_conf(self) -> None:
        # generate_config writes per-site confs but never prunes; drop the old one.
        (self.bench.config_path / "nginx" / "sites" / f"{self.old_name}.conf").unlink(missing_ok=True)

    def _add_to_hosts(self) -> None:
        import subprocess
        from pathlib import Path

        if not self.bench.config.production.process_manager == "none":
            return
        hosts_path = Path("/etc/hosts")
        entry = f"127.0.0.1 {self.new_name}"
        for line in hosts_path.read_text().splitlines():
            if entry in line.split("#", 1)[0].split():
                return
        subprocess.run(
            ["sudo", "tee", "-a", str(hosts_path)],
            input=f"{entry}\n".encode(),
            capture_output=True,
            check=True,
        )

    def _reload_nginx(self) -> None:
        if not self.bench.config.production.enabled:
            return
        from pilot.managers.nginx_manager import NginxManager

        mgr = NginxManager(self.bench)
        if not mgr.is_installed():
            return
        print("Updating nginx configuration...")
        sys.stdout.flush()
        mgr.generate_config(ssl_ready=True)
        mgr.reload()

    def _run_followups(self, ssl_enabled: bool) -> None:
        # Auto-run whatever the new domain needs; on failure point the user at the
        # manual command. setup production already reissues certs for ssl sites,
        # so letsencrypt is only run separately when the bench isn't in prod.
        name = self.bench.config.name
        if self.bench.config.production.enabled:
            from pilot.commands.setup.production import SetupProductionCommand

            self._run_or_advise("production setup", lambda: SetupProductionCommand(self.bench).run(),
                                 f"bench setup production -b {name}")
        elif ssl_enabled:
            from pilot.commands.setup.letsencrypt import SetupLetsEncryptCommand

            self._run_or_advise("Let's Encrypt setup", lambda: SetupLetsEncryptCommand(self.bench).run(),
                                 f"bench setup letsencrypt -b {name}")

    def _run_or_advise(self, label: str, fn, manual_cmd: str) -> None:
        print(f"\nRunning {label} for the new domain...")
        sys.stdout.flush()
        try:
            fn()
        except (Exception, SystemExit) as exc:
            detail = f" ({exc})" if str(exc) else ""
            print(f"\n{label} did not complete{detail}. Run it yourself once resolved:\n  {manual_cmd}")
