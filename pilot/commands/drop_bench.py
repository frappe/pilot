from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.commands.base import Command
from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class DropBenchCommand(Command):
    name = "drop"
    help = "Delete a bench (must have no sites), tearing down its production services, nginx, and dedicated database instance."
    # Deleting whichever bench happens to be active by default would be too easy
    # to trigger by accident, so require an explicit -b/--bench (or running from
    # inside the bench dir).
    requires_explicit_bench = True

    @classmethod
    def from_args(cls, args, bench):
        return cls(bench, skip_confirm=args.yes)

    def __init__(self, bench: "Bench", skip_confirm: bool = False) -> None:
        self.bench = bench
        self.skip_confirm = skip_confirm

    def run(self) -> None:
        name = self.bench.config.name
        self._validate_no_sites(name)
        self._confirm(name)

        self._remove_production()
        self._release_admin_domain()
        if self.bench.config.db_type == "postgres":
            self._teardown_postgres()
        else:
            self._teardown_mariadb()
        # Best-effort: benches created before ZFS/volume support was removed may
        # still have their directory bind-mounted from an old dataset. No-op for
        # any bench that was never volume-backed.
        self._unmount_legacy_bind_mount(self.bench.path)
        self._delete_bench_dir()
        print(f"\nBench '{name}' dropped.")

    def _teardown_mariadb(self) -> None:
        # A dedicated MariaDB instance is only ours to destroy when no other
        # bench connects to it.
        dedicated = bool(self.bench.config.mariadb.instance)
        full_db_teardown = dedicated and not self._mariadb_shared_with_other_bench()
        if full_db_teardown:
            from pilot.managers.mariadb_manager import MariaDBManager

            self._stop_mariadb()
            self._unmount_legacy_bind_mount(Path(MariaDBManager(self.bench.config.mariadb).data_dir()))
            self._remove_mariadb_instance()
        elif dedicated:
            print("Keeping MariaDB instance — another bench shares it.")

    def _teardown_postgres(self) -> None:
        # Only a dedicated cluster needs teardown, and only when no other bench
        # connects to it.
        if not self.bench.config.postgres.instance:
            return
        if self._postgres_shared_with_other_bench():
            print("Keeping PostgreSQL cluster — another bench shares it.")
            return
        self._remove_postgres_instance()

    def _release_admin_domain(self) -> None:
        """Release the admin domain that setup-production registered with the domain
        provider, so dropping the bench leaves no dead route at the edge."""
        from pilot.core.domain_controller import DomainRouteProvider

        domain = self.bench.config.admin.domain
        if domain:
            DomainRouteProvider(self.bench).release(domain)

    def _validate_no_sites(self, name: str) -> None:
        sites = self.bench.sites()
        if sites:
            listed = ", ".join(s.config.name for s in sites)
            raise BenchError(
                f"Bench '{name}' still has {len(sites)} site(s): {listed}. "
                f"Drop them first, then retry."
            )

    def _confirm(self, name: str) -> None:
        if self.skip_confirm:
            return
        answer = input(f"Permanently delete bench '{name}' and its database? [y/N] ")
        if answer.strip().lower() not in ("y", "yes"):
            raise BenchError("Aborted.")

    def _remove_production(self) -> None:
        if not self.bench.config.production.enabled:
            return
        from pilot.commands.remove.production import RemoveProductionCommand

        RemoveProductionCommand(self.bench).run()

    def _stop_mariadb(self) -> None:
        """Stop the dedicated instance so its datadir bind can be unmounted and
        the dataset destroyed without a busy mount."""
        from pilot.managers.mariadb_manager import MariaDBManager

        try:
            MariaDBManager(self.bench.config.mariadb).stop()
        except Exception as exc:
            print(f"  (mariadb stop skipped: {exc})")

    def _remove_mariadb_instance(self) -> None:
        from pilot.managers.mariadb_manager import MariaDBManager

        print("Removing MariaDB instance...")
        sys.stdout.flush()
        try:
            MariaDBManager(self.bench.config.mariadb).remove_instance()
        except Exception as exc:  # never block the drop on a best-effort cleanup
            print(f"  (mariadb cleanup skipped: {exc})")

    def _remove_postgres_instance(self) -> None:
        from pilot.managers.postgres_manager import PostgresManager

        print("Removing PostgreSQL cluster...")
        sys.stdout.flush()
        try:
            PostgresManager(self.bench.config.postgres).remove_instance()
        except Exception as exc:  # never block the drop on a best-effort cleanup
            print(f"  (postgres cleanup skipped: {exc})")

    def _postgres_shared_with_other_bench(self) -> bool:
        """True if another bench connects to this bench's PostgreSQL cluster (same
        cluster name or host:port), so removing it would break that bench."""
        from pilot.utils import iter_sibling_benches

        mine = self.bench.config.postgres
        if not mine.instance:
            return False
        mine_keys = self._postgres_identity(mine)
        for _, cfg in iter_sibling_benches(self.bench.path):
            other = getattr(cfg, "postgres", None)
            if other and other.instance and (mine_keys & self._postgres_identity(other)):
                return True
        return False

    @staticmethod
    def _postgres_identity(cfg) -> set:
        return {("instance", cfg.instance), ("tcp", DropBenchCommand._normalize_db_host(cfg.host), cfg.port)}

    def _mariadb_shared_with_other_bench(self) -> bool:
        """True if another bench connects to this bench's MariaDB — whether by
        dedicated instance name, datadir, unix socket, or host:port (a bench can
        be pointed at a sibling's database over any of these). Removing the
        instance would break that bench, so we leave its data in place."""
        from pilot.utils import iter_sibling_benches

        mine = self.bench.config.mariadb
        if not mine.instance:
            return False  # shared server: remove_instance is a no-op regardless

        mine_keys = self._mariadb_identity(mine)
        for _, cfg in iter_sibling_benches(self.bench.path):
            other = getattr(cfg, "mariadb", None)
            if other and (mine_keys & self._mariadb_identity(other)):
                return True
        return False

    @staticmethod
    def _mariadb_identity(cfg) -> set:
        """The set of connection identities a MariaDB config resolves to. Two
        benches share a database iff their identity sets intersect."""
        from pilot.managers.mariadb_manager import MariaDBManager

        keys = {("tcp", DropBenchCommand._normalize_db_host(cfg.host), cfg.port)}
        if cfg.instance:
            mgr = MariaDBManager(cfg)
            keys |= {
                ("instance", cfg.instance),
                ("datadir", mgr.data_dir()),
                ("socket", mgr.instance_socket()),
            }
        if cfg.socket_path:
            keys.add(("socket", cfg.socket_path))
        if cfg.data_dir:
            keys.add(("datadir", cfg.data_dir))
        return keys

    @staticmethod
    def _normalize_db_host(host: str) -> str:
        """Fold the literal loopback aliases to one key. Sharing is decided from the
        declared config of the benches we manage — instance, datadir, socket, and
        host:port — never by probing the network to classify an address. The DB a
        dedicated instance owns is identified by the datadir/instance/socket it
        physically holds, which siblings carry in their own config; a DB created by
        some external, unmanaged process is out of scope. Network probing was both
        unreliable (offline/firewalled/cloud hosts) and unnecessary."""
        host = (host or "").strip().lower()
        return "127.0.0.1" if host in ("", "localhost", "127.0.0.1", "::1") else host

    def _delete_bench_dir(self) -> None:
        path = self.bench.path
        print(f"Deleting {path}...")
        sys.stdout.flush()
        shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def _unmount_legacy_bind_mount(target: Path, fstab_path: Path = Path("/etc/fstab")) -> None:
        """Unmount `target` and drop its fstab entry if present.

        Benches created before ZFS/volume support was removed may still have
        their directory (or a dedicated MariaDB datadir) bind-mounted from an
        old dataset, with a matching fstab line so it survived reboots. This
        doesn't depend on ZFS or any volume-management code being present —
        it only looks at whether `target` is currently a mountpoint — so it's
        a no-op, and safe to call unconditionally, for any bench that was
        never volume-backed.
        """
        try:
            is_mounted = target.is_mount()
        except OSError:
            is_mounted = False
        if is_mounted:
            print(f"Unmounting legacy bind mount at {target}...")
            sys.stdout.flush()
            try:
                subprocess.run(["sudo", "umount", "-l", str(target)], check=False)
            except Exception as exc:
                print(f"  (unmount {target} skipped: {exc})")

        try:
            lines = fstab_path.read_text().splitlines()
        except OSError:
            return
        kept = [
            line for line in lines
            if not (
                len(line.split()) >= 2
                and not line.lstrip().startswith("#")
                and line.split()[1] == str(target)
            )
        ]
        if len(kept) == len(lines):
            return
        content = "\n".join(kept) + "\n"
        try:
            subprocess.run(["sudo", "tee", str(fstab_path)], input=content.encode(), capture_output=True, check=True)
        except Exception as exc:
            print(f"  (fstab cleanup for {target} skipped: {exc})")
