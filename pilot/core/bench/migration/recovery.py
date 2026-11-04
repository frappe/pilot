from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.core.bench.migration.state import MigrationStateError
from pilot.exceptions import BenchError
from pilot.internal.jsonl_log import utc_now

if TYPE_CHECKING:
    from pilot.core.bench.migration.operation import OnProgress, OnStep


def _no_step(key: str, label: str) -> None:
    return None


def _no_progress(message: str) -> None:
    return None


class MigrationRecovery:
    """Recovery actions of a paused MigrationOperation: retry, skip a patch, restore, dismiss."""

    def retry(self) -> None:
        """Resume the chain from needs_attention so the failed unit runs again."""
        if self.state != "needs_attention":
            raise MigrationStateError(f"Retry is not allowed from state {self.state}")
        phase = self.return_state or "migrating"
        self._prepare_sites()
        self.diagnosis = None
        self._transition("retrying")
        if phase == "backing_up":
            # Sites were live after the failure, so every snapshot may be stale.
            for site in self.sites:
                site.backup_status = "pending"
        elif phase == "migrating" and self.failed_site:
            self.site(self.failed_site).migration_status = "pending"
        self.failed_site = None
        self._transition(phase)

    def bypass_patch(self, patch: str, on_progress: "OnProgress" = _no_progress) -> None:
        """Permanently mark one patch as completed for the failed site via Frappe.

        Re-arms the chain so the caller can resume migration immediately: the
        failed site goes back to pending and the operation returns to migrating.
        """
        from pilot.utils import run_command

        if self.state != "needs_attention" or not self.failed_site:
            raise MigrationStateError("Skip patch is only available on a failed migration.")
        diagnosed_patch = (self.diagnosis or {}).get("patch")
        if not diagnosed_patch or patch != diagnosed_patch:
            raise MigrationStateError("Skip patch must match the diagnosed failing patch.")
        on_progress(f"Skipping patch {patch} on {self.failed_site}...")
        command = [
            *self.bench.frappe_call,
            "frappe",
            "--site",
            self.failed_site,
            "bypass-patch",
            patch,
            "--yes",
        ]
        result = run_command(command, cwd=self.bench.sites_path, tee_output=True)
        if result.returncode != 0:
            raise BenchError(
                f"bypass-patch failed for {patch} (exit {result.returncode}). "
                "This Frappe version may not support bypass-patch."
            )
        failed = self.site(self.failed_site)
        failed.touched_tables = sorted(set(failed.touched_tables) | {"tabPatch Log"})
        self.decisions.append(
            {"action": "bypass_patch", "site": self.failed_site, "patch": patch, "at": utc_now()}
        )
        self._save()
        self.bench.audit_action(
            "bypass_patch", {"operation": self.id, "site": self.failed_site, "patch": patch}
        )
        self.retry()

    def revert(self) -> None:
        """Resume from needs_attention/revert_failed and enter the next revert phase."""
        if self.state not in ("needs_attention", "revert_failed"):
            raise MigrationStateError(f"Restore is not allowed from state {self.state}")
        if not self.can_revert:
            raise BenchError("Restore is unavailable: safeguards were not created for this update.")
        self.diagnosis = None
        self._advance_revert()

    def next_revert_site(self) -> str | None:
        return next(
            (
                site.name
                for site in self.sites
                if site.migration_status != "pending"
                and not self.revert_checkpoints.get(f"site:{site.name}")
                and self.bench.site(site.name).exists
            ),
            None,
        )

    def revert_apps(self, on_step: "OnStep" = _no_step, on_progress: "OnProgress" = _no_progress) -> None:
        on_step("revert_apps", "Reverting app revisions")
        try:
            for revision in self.apps:
                if not (self.bench.apps_path / revision.name).is_dir():
                    on_progress(f"Skipping {revision.name}: it was removed from the bench after the update.")
                    continue
                on_progress(f"Reverting {revision.name} to {revision.branch or revision.sha[:8]}...")
                app = self.bench.app(revision.name)
                if revision.branch:
                    app.return_to(revision.branch, revision.sha)
                    app.record_branch()
                else:
                    app.checkout_commit(revision.sha)
            if self.apps:
                filter_set = set(self.apps_filter) if self.apps_filter else None
                self.bench._reinstall_apps(filter_set, on_progress)
                self.bench._rebuild_assets(filter_set, on_progress)
            self.revert_checkpoints["apps"] = True
            self._save()
        except Exception as error:
            self._fail_revert(error)
            raise
        self._advance_revert()

    def revert_site(
        self, name: str, on_step: "OnStep" = _no_step, on_progress: "OnProgress" = _no_progress
    ) -> None:
        site = self.site(name)
        on_step("revert_site", f"Recovering {name}")
        site.migration_status = "recovering"
        self._save()
        try:
            on_progress(f"Restoring database for {name}...")
            tables = site.touched_tables if site.touched_tables_trusted else []
            self.bench.site(name).migration_backup.restore(tables)
            on_progress(f"Clearing cache for {name}...")
            self.bench.site(name).clear_cache()
            site.migration_status = "recovered"
            self.revert_checkpoints[f"site:{name}"] = True
            self._save()
        except Exception as error:
            self._fail_revert(error)
            raise
        self._advance_revert()

    def restart(self, on_step: "OnStep" = _no_step) -> None:
        on_step("restart", "Restarting services")
        try:
            self._restart_services()
            self._discard_snapshots()
            self.revert_checkpoints["restarted"] = True
        except Exception as error:
            self._fail_revert(error)
            raise
        self._transition("reverted")

    def _advance_revert(self) -> None:
        """Enter the next revert phase, staying put while the current one has more units to do."""
        phase = self._next_revert_phase()
        if phase != self.state:
            self._transition(phase)

    def _next_revert_phase(self) -> str:
        if self.apps and not self.revert_checkpoints.get("apps"):
            return "reverting_apps"
        if self.next_revert_site() is not None:
            return "reverting_sites"
        return "restarting"

    def _fail_revert(self, error: Exception) -> None:
        self.diagnosis = {"message": f"Revert failed: {error}", "output_excerpt": ""}
        self._transition("revert_failed")

    def dismiss(self) -> None:
        """Close a paused operation nobody will recover. Services restart on the code now on disk,
        sites leave maintenance and the recovery snapshots are deleted; the record stays as history."""
        if not self.state.is_failure:
            raise MigrationStateError(f"Dismiss is not allowed from state {self.state}")
        self._restart_services()
        self._discard_snapshots()
        self.decisions.append({"action": "dismiss", "state": str(self.state), "at": utc_now()})
        self._transition("dismissed")
        self.bench.audit_action("dismiss_migration", {"operation": self.id})
