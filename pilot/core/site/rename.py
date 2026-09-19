from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError
from pilot.utils import normalize_host, write_private_text

if TYPE_CHECKING:
    from pilot.config import RoutePolicy, SiteConfig
    from pilot.core.site import Site


class SiteRename:
    """Rename a site without dropping requests."""

    def __init__(self, site: "Site", new_name: str, keep_old_hostname: bool = True) -> None:
        self.site = site
        self.bench = site.bench
        self.old_name = site.config.name
        self.new_name = new_name
        self.keep_old_hostname = keep_old_hostname
        self._compatibility_link: Path | None = None
        self._original_site_config: str | None = None
        self._old_pilot_auth_token = ""
        self._new_route: RoutePolicy | None = None

    @property
    def new_path(self) -> Path:
        return self.bench.sites_path / self.new_name

    def run(self, on_progress: Callable[[str], None]) -> None:
        self.validate()

        on_progress(f"Renaming site '{self.old_name}' -> '{self.new_name}'...")
        # Register first so a provider failure needs no rollback.
        self._register_route(self.new_name)
        try:
            self._move_site_directory()
        except BaseException:
            self._release_route(self.new_name)
            raise
        try:
            self._pin_certificate_name()
            self._carry_old_hostname()
            self._refresh_pilot_auth_token()
            self._update_default_site(self.old_name, self.new_name)
            self._rename_in_bench_toml(self.old_name, self.new_name)
            self._retarget_hostname_aliases(self.old_name, self.new_name)
            self._add_to_hosts()
            # Reload only after both names resolve on disk.
            self._reload_nginx()
            self._drop_compatibility_link()
        except BaseException:
            # nginx still names the old site, so restore that state.
            self._roll_back(on_progress)
            raise
        # Revocation can commit before its durability sync reports failure. The rename
        # must stay committed once this starts, or rollback could restore a dead token.
        self._revoke_old_pilot_auth_token()
        # Release the old route only after nginx has switched.
        if not self.keep_old_hostname:
            self._release_route(self.old_name)

        on_progress(f"\nSite renamed to '{self.new_name}'.")
        self.run_followups(on_progress)
        self._report_reserved_hostname(on_progress)

    def validate(self) -> None:
        from pilot.utils import host_owner

        if self.new_name == self.old_name:
            raise BenchError("New name is the same as the current name.")

        sites = {site.config.name: site for site in self.bench.sites()}
        if self.old_name not in sites:
            raise BenchError(f"Site '{self.old_name}' does not exist in this bench.")

        if self.new_name in sites or self.new_path.exists() or self.new_path.is_symlink():
            raise BenchError(f"Site '{self.new_name}' already exists in this bench.")

        # Read and retain the config before any write so rollback is lossless.
        config_path = self.site.path / "site_config.json"
        _load_site_config(config_path)
        self._original_site_config = config_path.read_text()

        # Check this bench separately because host_owner only sees siblings.
        if claimed_by := self.bench.site_claiming(self.new_name, ignoring=self.old_name):
            raise BenchError(
                f"'{self.new_name}' is already a domain of this bench's site '{claimed_by}'. "
                f"All benches share one nginx, so hostnames must be unique."
            )
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

    def _register_route(self, hostname: str) -> None:
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        self._new_route = DomainRouteProvider(self.bench).register(hostname, hostname)

    def _release_route(self, hostname: str) -> None:
        """Best effort: the provider warns rather than raising."""
        from pilot.core.adapters.domain_provider import DomainRouteProvider

        DomainRouteProvider(self.bench).release(hostname)

    def _move_site_directory(self) -> None:
        """Move the site while keeping its old path as a symlink."""
        old_path = self.site.path
        staged = self.bench.sites_path / f".renaming-{self.new_name}"
        staged.unlink(missing_ok=True)
        staged.symlink_to(self.new_name)  # dangling until the move below lands
        try:
            old_path.rename(self.new_path)
        except BaseException:
            staged.unlink(missing_ok=True)
            raise
        try:
            staged.rename(old_path)
        except BaseException:
            self.new_path.rename(old_path)
            staged.unlink(missing_ok=True)
            raise
        self._compatibility_link = old_path

    def _drop_compatibility_link(self) -> None:
        """Once nginx names the new site, nothing asks for the old path again."""
        link = self._compatibility_link
        if link is not None and link.is_symlink():
            link.unlink()
        self._compatibility_link = None

    def _roll_back(self, on_progress: Callable[[str], None]) -> None:
        """Restore the old name, attempting every rollback step."""
        steps = (
            ("the site config", self._restore_site_config),
            ("common_site_config.json", lambda: self._update_default_site(self.new_name, self.old_name)),
            ("bench.toml", lambda: self._rename_in_bench_toml(self.new_name, self.old_name)),
            ("the hostname aliases", lambda: self._retarget_hostname_aliases(self.new_name, self.old_name)),
            ("the site directory", self._move_site_directory_back),
            ("the provider route", lambda: self._release_route(self.new_name)),
            ("nginx", self._reload_nginx),
        )
        failed = []
        for label, step in steps:
            try:
                step()
            except Exception as exc:
                failed.append(label)
                on_progress(f"Could not restore {label}: {exc}")
        if failed:
            on_progress(
                f"\nThe rename was only partly rolled back ({', '.join(failed)}). Check the site before retrying."
            )
        else:
            on_progress(f"\nRolled the rename back; the site is '{self.old_name}' again.")

    def _restore_site_config(self) -> None:
        """Byte for byte, undoing the pin and the carried hostname at once."""
        if self._original_site_config is not None:
            write_private_text(self.new_path / "site_config.json", self._original_site_config)

    def _move_site_directory_back(self) -> None:
        """Restore the real directory at the old path."""
        old_path = self.site.path
        if old_path.is_symlink():
            old_path.unlink()
        self.new_path.rename(old_path)
        self._compatibility_link = None

    def _pin_certificate_name(self) -> None:
        """Keep an existing certificate lineage across a rename."""
        from pilot.managers.nginx import cert_files_exist

        config = self._read_site_config()
        if config.get("cert_name") or not cert_files_exist(self.old_name):
            return
        if not self.keep_old_hostname and not self._tls_domains_besides(config, self.old_name, self.new_name):
            # Do not pin a lineage for a hostname being returned to the pool.
            return
        config["cert_name"] = self.old_name
        self._write_site_config(config)

    def _tls_domains_besides(self, config: dict, *excluded: str) -> list[str]:
        """The site's TLS domains as it will be, ignoring the named hostnames."""
        skip = {normalize_host(name) for name in excluded}
        return [
            domain
            for domain in self._site_config_from(config).tls_domains
            if normalize_host(domain) not in skip
        ]

    def _site_config_from(self, config: dict) -> "SiteConfig":
        from pilot.config import RoutePolicy, SiteConfig

        return SiteConfig(
            name=self.new_name,
            apps=[],
            domains=config.get("domains") or [],
            ssl=bool(config.get("ssl")),
            route=RoutePolicy.from_dict(config["route"]) if config.get("route") else None,
        )

    def _carry_old_hostname(self) -> None:
        """Keep the old hostname and move its canonical setting if needed."""
        config = self._read_site_config()
        # The new name is the site's own now, so it is no longer one of its
        # aliases; left there it would repeat inside one server_name.
        domains = [
            entry
            for entry in (config.get("domains") or [])
            if normalize_host(_domain_name(entry)) != normalize_host(self.new_name)
        ]
        known = {normalize_host(_domain_name(entry)) for entry in domains}
        if self.keep_old_hostname and normalize_host(self.old_name) not in known:
            old_route = self.site.config.route
            domains.append(
                {"domain": self.old_name, "route": old_route.to_dict()}
                if old_route
                else self.old_name
            )
        config["domains"] = domains
        if self._new_route:
            config["route"] = self._new_route.to_dict()
            config["ssl"] = self._new_route.public_tls

        # A canonical host naming the old site has to move with it, or nginx
        # redirects every request to a hostname this site no longer answers to.
        primary = (config.get("host_name") or "").split("://", 1)[-1]
        if primary and normalize_host(primary) == normalize_host(self.old_name):
            scheme = self._new_route.public_scheme if self._new_route else (
                "https" if config.get("ssl") else "http"
            )
            config["host_name"] = f"{scheme}://{self.new_name}"
        self._write_site_config(config)

    def _serves_tls(self) -> bool:
        """Whether any site domain terminates TLS on this host."""
        return bool(self._site_config_from(self._read_site_config()).tls_domains)

    def _refresh_pilot_auth_token(self) -> None:
        config = self._read_site_config()
        token = config.get("pilot_auth_token")
        if not isinstance(token, str) or not token:
            return
        from admin.backend.internal.session import Session

        self._old_pilot_auth_token = token
        config["pilot_auth_token"] = Session(self.bench).issue_pilot_token(self.new_name)
        self._write_site_config(config)

    def _revoke_old_pilot_auth_token(self) -> None:
        if not self._old_pilot_auth_token:
            return
        from admin.backend.internal.session import Session

        Session(self.bench).revoke_token(self._old_pilot_auth_token)

    def _read_site_config(self) -> dict:
        return _load_site_config(self.new_path / "site_config.json")

    def _write_site_config(self, config: dict) -> None:
        write_private_text(self.new_path / "site_config.json", json.dumps(config, indent=1))

    def run_followups(self, on_progress: Callable[[str], None]) -> None:
        """Issue a certificate for the new hostname after nginx publishes it."""
        if not self._serves_tls():
            return
        self._run_or_advise(
            "Let's Encrypt setup",
            self.bench.setup_letsencrypt,
            f"pilot setup letsencrypt -b {self.bench.config.name}",
            on_progress,
        )

    def _report_reserved_hostname(self, on_progress: Callable[[str], None]) -> None:
        """Report when a released hostname remains reserved by its certificate."""
        pinned = self._read_site_config().get("cert_name")
        if self.keep_old_hostname or not pinned:
            return
        on_progress(
            f"\n'{pinned}' stays reserved for this site's certificate until one is issued under "
            f"'{self.new_name}'. Run 'pilot setup letsencrypt -b {self.bench.config.name}' once "
            f"that can succeed, and the name is freed."
        )

    def _update_default_site(self, current: str, target: str) -> None:
        path = self.bench.sites_path / "common_site_config.json"
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return
        if data.get("default_site") == current:
            data["default_site"] = target
            write_private_text(path, json.dumps(data, indent=2) + "\n")

    def _rename_in_bench_toml(self, current: str, target: str) -> None:
        from pilot.config import BenchConfig

        with BenchConfig.open(self.bench.path, mode="raw") as raw:
            for site in raw.get("sites", []):
                if site.get("name") == current:
                    site["name"] = target

    def _retarget_hostname_aliases(self, current: str, target: str) -> None:
        self.bench.hostname_aliases.retarget("site", current, target)

    def _add_to_hosts(self) -> None:
        if self.bench.config.production.process_manager != "none":
            return
        from pilot.managers.platform import add_hosts_entry

        add_hosts_entry(self.new_name)

    def _reload_nginx(self) -> None:
        from pilot.managers.nginx import NginxManager

        NginxManager(self.bench).reload_for_site_change()

    def _run_or_advise(
        self,
        label: str,
        fn,
        manual_cmd: str,
        on_progress: Callable[[str], None],
    ) -> None:
        on_progress(f"\nRunning {label} for the new domain...")
        try:
            fn()
        except (Exception, SystemExit) as exc:
            detail = f" ({exc})" if str(exc) else ""
            on_progress(f"\n{label} did not complete{detail}. Run it yourself once resolved:\n  {manual_cmd}")


def _domain_name(entry) -> str:
    return entry.get("domain", "") if isinstance(entry, dict) else str(entry)


def _load_site_config(path: Path) -> dict:
    """Load a site config or fail before any rename writes."""
    try:
        raw = json.loads(path.read_text())
    except OSError as exc:
        raise BenchError(f"Could not read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise BenchError(f"{path} is not valid JSON ({exc}). Fix it before renaming the site.") from exc
    if not isinstance(raw, dict):
        raise BenchError(f"{path} must hold a JSON object.")
    return raw
