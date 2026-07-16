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
    help = "Delete a bench (must have no sites), tearing down its production services and nginx."
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
        # No per-bench database to tear down: every bench for this OS user
        # shares one MariaDB/PostgreSQL server (see MariaDBManager/
        # PostgresManager), and _validate_no_sites above already guarantees
        # this bench has no sites — and therefore no databases — left.
        # Best-effort: benches created before ZFS/volume support was removed may
        # still have their directory bind-mounted from an old dataset. No-op for
        # any bench that was never volume-backed.
        self._unmount_legacy_bind_mount(self.bench.path)
        self._delete_bench_dir()
        print(f"\nBench '{name}' dropped.")

    def _release_admin_domain(self) -> None:
        """Release the admin domain that setup-production registered with the domain
        provider, so dropping the bench leaves no dead route at the edge."""
        from pilot.core.domains import DomainRouteProvider

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
        from pilot.managers.platform import _privileged

        try:
            is_mounted = target.is_mount()
        except OSError:
            is_mounted = False
        if is_mounted:
            print(f"Unmounting legacy bind mount at {target}...")
            sys.stdout.flush()
            try:
                subprocess.run(_privileged(["umount", "-l", str(target)]), check=False)
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
            subprocess.run(
                _privileged(["tee", str(fstab_path)]),
                input=content.encode(),
                capture_output=True,
                check=True,
            )
        except Exception as exc:
            print(f"  (fstab cleanup for {target} skipped: {exc})")
