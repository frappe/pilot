from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands.base import Arg, Command
from pilot.exceptions import BenchError
from pilot.secure_files import write_private_text


@dataclass(kw_only=True)
class RenameSiteCommand(Command):
    name: ClassVar[str] = "rename-site"
    help: ClassVar[str] = "Rename a site in this bench."

    old_name: Annotated[str, Arg(help="Current site name.")]
    new_name: Annotated[str, Arg(help="New site name (hostname).")]

    def run(self) -> None:
        from pilot.managers.nginx import NginxManager

        old_site = self._validate()
        ssl_enabled = old_site.config.ssl

        self.print(f"Renaming site '{self.old_name}' -> '{self.new_name}'...")
        old_site.path.rename(self.bench.sites_path / self.new_name)

        self._update_default_site()
        self._rename_in_bench_toml()
        self._remove_stale_nginx_conf()
        self._add_to_hosts()
        NginxManager(self.bench).reload_for_site_change()

        self.print(f"\nSite renamed to '{self.new_name}'.")
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
        except (OSError, json.JSONDecodeError):
            return
        if data.get("default_site") == self.old_name:
            data["default_site"] = self.new_name
            write_private_text(path, json.dumps(data, indent=2) + "\n")

    def _rename_in_bench_toml(self) -> None:
        from pilot.config.toml_store import BenchTomlStore

        store = BenchTomlStore.for_bench(self.bench.path)
        with store.edit_raw() as raw:
            for site in raw.get("sites", []):
                if site.get("name") == self.old_name:
                    site["name"] = self.new_name

    def _remove_stale_nginx_conf(self) -> None:
        # generate_config writes per-site confs but never prunes; drop the old one.
        (self.bench.config_path / "nginx" / "sites" / f"{self.old_name}.conf").unlink(missing_ok=True)

    def _add_to_hosts(self) -> None:
        if not self.bench.config.production.process_manager == "none":
            return

        from pilot.managers.platform import add_hosts_entry

        add_hosts_entry(self.new_name)

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
        self.print(f"\nRunning {label} for the new domain...")
        try:
            fn()
        except (Exception, SystemExit) as exc:
            detail = f" ({exc})" if str(exc) else ""
            self.print(f"\n{label} did not complete{detail}. Run it yourself once resolved:\n  {manual_cmd}")
