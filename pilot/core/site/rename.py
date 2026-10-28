from __future__ import annotations

import contextlib
import errno
import json
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError, CronError
from pilot.internal.atomic_file import exchange_paths, exclusive_file_lock, replace_private_text_locked
from pilot.utils import make_private_directory, normalize_host

if TYPE_CHECKING:
    from pilot.config import RoutePolicy, SiteConfig
    from pilot.core.site import Site


class SiteRename:
    """Rename a site without dropping requests."""

    def __init__(
        self, site: "Site", new_name: str, keep_old_hostname: bool = True, make_primary: bool = False
    ) -> None:
        self.site = site
        self.bench = site.bench
        self.old_name = site.config.name
        self.new_name = new_name
        for name in (self.old_name, self.new_name):
            if not name or name in {".", ".."} or Path(name).name != name:
                raise BenchError("Site names must stay within the bench's sites directory.")
        self.keep_old_hostname = keep_old_hostname
        self.make_primary = make_primary
        self._original_site_config: str | None = None
        self._old_pilot_auth_token = ""
        self._new_route: RoutePolicy | None = None

    @property
    def new_path(self) -> Path:
        return self.bench.sites_path / self.new_name

    @property
    def progress_path(self) -> Path:
        return self.bench.config_path / "site-renames" / f"{self.old_name}.json"

    def run(self, on_progress: Callable[[str], None]) -> None:
        make_private_directory(self.progress_path.parent, parents=True)
        with exclusive_file_lock(self.progress_path.parent / "operations"):
            self._prepare()
            on_progress(f"Renaming site '{self.old_name}' -> '{self.new_name}'...")
            self._move_or_resume()
            try:
                self._finish(on_progress)
            except BaseException:
                on_progress("The rename is pending. The old path is retained. Retry this task to finish it.")
                raise

    def _prepare(self) -> None:
        if self.progress_path.exists():
            progress = _load_site_config(self.progress_path)
            expected = [self.new_name, self.keep_old_hostname, self.make_primary]
            if progress["target"] != expected:
                raise BenchError("A different rename is pending for this site. Finish that rename first.")
            self._original_site_config = progress["site_config"]
            if progress["route"]:
                from pilot.config import RoutePolicy

                self._new_route = RoutePolicy.from_dict(progress["route"])
            return
        for path in self.progress_path.parent.glob("*.json"):
            if _load_site_config(path)["target"][0] == self.old_name:
                raise BenchError("This site's previous rename is pending. Finish that rename first.")
        self.validate()
        self.bench.site_aliases.release(self.new_name)
        self._register_route(self.new_name)
        replace_private_text_locked(
            self.progress_path,
            json.dumps(
                {
                    "target": [self.new_name, self.keep_old_hostname, self.make_primary],
                    "site_config": self._original_site_config,
                    "route": self._new_route.to_dict() if self._new_route else None,
                }
            ),
        )

    @property
    def is_directory_moved(self) -> bool:
        return (
            self.site.path.is_symlink()
            and self.site.path.readlink() == Path(self.new_name)
            and self.new_path.is_dir()
            and not self.new_path.is_symlink()
        )

    def _move_or_resume(self) -> None:
        if self.is_directory_moved:
            return
        if not self.site.path.is_dir() or self.site.path.is_symlink():
            raise BenchError("The pending rename's original directory is unavailable.")
        if self.new_path.is_symlink() and self.new_path.readlink() == Path(self.new_name):
            self.new_path.unlink()
        self._move_site_directory()

    def _finish(self, on_progress: Callable[[str], None]) -> None:
        self.bench.site_aliases.retarget(self.old_name, self.new_name)
        self._pin_certificate_name()
        self._carry_old_hostname()
        self._refresh_pilot_auth_token()
        self._rename_email_sender_name()
        self._update_default_site(self.old_name, self.new_name)
        self._rename_in_bench_toml(self.old_name, self.new_name)
        self._retarget_hostname_aliases(self.old_name, self.new_name)
        if self.make_primary:
            self.bench.hostname_aliases.start_redirecting("site", self.new_name)
        self._add_to_hosts()
        self._reload_nginx()
        if self._update_primary_hostname():
            self._reload_nginx()
        self._revoke_old_pilot_auth_token()
        try:
            self.bench.site(self.new_name).backups.move_schedule(self.old_name)
        except CronError as error:
            on_progress(f"Could not move the backup schedule to the new name: {error}")
        # Release the old route only after nginx has switched.
        if not self.keep_old_hostname:
            self._release_route(self.old_name)

        on_progress(f"\nSite renamed to '{self.new_name}'.")
        self.run_followups(on_progress)
        self._report_reserved_hostname(on_progress)
        self.bench.site_aliases.record(self.old_name)
        self.progress_path.unlink()

    def validate(self) -> None:
        from pilot.utils import host_owner

        if self.new_name == self.old_name:
            raise BenchError("New name is the same as the current name.")

        sites = {site.config.name: site for site in self.bench.sites()}
        if self.old_name not in sites:
            raise BenchError(f"Site '{self.old_name}' does not exist in this bench.")

        if self.new_name in sites or (self.new_path.exists() and not self.new_path.is_symlink()):
            raise BenchError(f"Site '{self.new_name}' already exists in this bench.")

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
        self.new_path.symlink_to(self.new_name)
        try:
            self.bench.site_aliases.record(self.old_name)
            exchange_paths(old_path, self.new_path)
        except BaseException as error:
            if self.new_path.is_symlink():
                self.new_path.unlink()
            if isinstance(error, OSError) and error.errno in (errno.ENOTSUP, errno.EINVAL, errno.ENOSYS):
                raise BenchError(
                    "This filesystem cannot swap the site directory atomically. The site was not renamed."
                ) from error
            raise

    def _pin_certificate_name(self) -> None:
        """Keep an existing certificate lineage across a rename."""
        from pilot.managers.nginx import cert_files_exist

        with self._updating_site_config() as config:
            if config.get("cert_name") or not cert_files_exist(self.old_name):
                return
            if not self.keep_old_hostname and not self._tls_domains_besides(
                config, self.old_name, self.new_name
            ):
                # Do not pin a lineage for a hostname being returned to the pool.
                return
            config["cert_name"] = self.old_name

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
        with self._updating_site_config() as config:
            # The new name is the site's own now, so it is no longer one of its
            # aliases; left there it would repeat inside one server_name.
            domains = [
                entry
                for entry in (config.get("domains") or [])
                if normalize_host(_domain_name(entry)) != normalize_host(self.new_name)
            ]
            known = {normalize_host(_domain_name(entry)) for entry in domains}
            if self.keep_old_hostname and normalize_host(self.old_name) not in known:
                from pilot.config import RoutePolicy

                route = json.loads(self._original_site_config or "{}").get("route")
                old_route = RoutePolicy.from_dict(route) if route else None
                domains.append(
                    {"domain": self.old_name, "route": old_route.to_dict()} if old_route else self.old_name
                )
            config["domains"] = domains
            if self._new_route:
                config["route"] = self._new_route.to_dict()
                config["ssl"] = self._new_route.public_tls

    def _update_primary_hostname(self) -> bool:
        with self._updating_site_config() as config:
            primary = (config.get("host_name") or "").split("://", 1)[-1]
            if self.make_primary or (primary and normalize_host(primary) == normalize_host(self.old_name)):
                scheme = (
                    self._new_route.public_scheme
                    if self._new_route
                    else ("https" if config.get("ssl") else "http")
                )
                target = f"{scheme}://{self.new_name}"
                if config.get("host_name") != target:
                    config["host_name"] = target
                    return True
        return False

    def _serves_tls(self) -> bool:
        """Whether any site domain terminates TLS on this host."""
        from pilot.managers.letsencrypt import certificate_domains

        return bool(certificate_domains(self._site_config_from(self._read_site_config())))

    def _refresh_pilot_auth_token(self) -> None:
        from admin.backend.internal.session import Session

        original = json.loads(self._original_site_config or "{}").get("pilot_auth_token")
        self._old_pilot_auth_token = original if isinstance(original, str) else ""
        with self._updating_site_config() as config:
            token = config.get("pilot_auth_token")
            if not self._old_pilot_auth_token or token != self._old_pilot_auth_token:
                return
            config["pilot_auth_token"] = Session(self.bench).issue_pilot_token(self.new_name)

    def _rename_email_sender_name(self) -> None:
        """Follow the new name unless someone set their own sender name."""
        from pilot.config.site import email_sender_name

        with self._updating_site_config() as config:
            if config.get("email_sender_name") == email_sender_name(self.old_name):
                config["email_sender_name"] = email_sender_name(self.new_name)

    def _revoke_old_pilot_auth_token(self) -> None:
        if not self._old_pilot_auth_token:
            return
        from admin.backend.internal.session import Session

        Session(self.bench).revoke_token(self._old_pilot_auth_token)

    def _read_site_config(self) -> dict:
        return _load_site_config(self.new_path / "site_config.json")

    @contextlib.contextmanager
    def _updating_site_config(self) -> Iterator[dict]:
        """Read, change and atomically write site_config.json under its lock."""
        path = self.new_path / "site_config.json"
        with exclusive_file_lock(path):
            config = _load_site_config(path)
            yield config
            replace_private_text_locked(path, json.dumps(config, indent=1))

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
        from pilot.config.common_site_config import update_common_site_config

        if not (self.bench.sites_path / "common_site_config.json").exists():
            return
        with update_common_site_config(self.bench.sites_path) as config:
            if config.get("default_site") == current:
                config["default_site"] = target

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
