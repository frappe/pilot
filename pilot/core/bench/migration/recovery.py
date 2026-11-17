from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.core.bench.migration.state import MigrationState, MigrationStateError
from pilot.exceptions import BenchError, TaskNotCancellableError, TaskNotRunningError
from pilot.internal.jsonl_log import utc_now

if TYPE_CHECKING:
    from pilot.core.bench import Bench
    from pilot.core.bench.migration.operation import AppRevision, OnProgress, OnStep, SiteProgress
    from pilot.core.bench.migration.store import MigrationStore


def _no_step(key: str, label: str) -> None:
    """A step callback that does nothing."""


def _no_progress(message: str) -> None:
    """A progress callback that does nothing."""


class MigrationRecovery:
    """User actions on a MigrationOperation: stop while it backs up, and retry, skip a patch, restore or dismiss once paused."""

    if TYPE_CHECKING:
        id: str
        state: MigrationState
        return_state: str | None
        failed_site: str | None
        diagnosis: dict | None
        sites: list[SiteProgress]
        apps: list[AppRevision]
        apps_filter: list[str] | None
        revert_checkpoints: dict
        decisions: list
        chain: list[dict]
        apps_updated: bool
        bench: Bench
        store: MigrationStore

        @property
        def can_revert(self) -> bool:
            """Whether every site has a backup to revert to."""

        def site(self, name: str) -> SiteProgress:
            """The progress of one site."""

        def _prepare_sites(self) -> None:
            """Save maintenance settings and turn maintenance on."""

        def _transition(self, target: str) -> None:
            """Move to the target state."""

        def _save(self) -> None:
            """Save the operation."""

        def _restart_services(self) -> None:
            """Restart the bench services."""

        def _discard_snapshots(self) -> None:
            """Discard the migration backups."""

        def _lift_maintenance(self) -> None:
            """Restore maintenance settings and refresh web workers."""

    @property
    def can_stop(self) -> bool:
        """Whether the migration may stop: it still takes backups and no app code has changed."""
        return self.state == "backing_up" and not self.apps_updated

    @property
    def is_stop_requested(self) -> bool:
        """Whether a stop was requested; the chain ends at its next step."""
        return self.store.is_stop_requested(self.id)

    def stop(self) -> None:
        """Stop a migration that takes backups. The killed backup task, or the chain at its next
        step, finishes the stop; raise when the chain has already started to change the bench."""
        if not self.can_stop:
            raise MigrationStateError(
                f"A migration can stop only while it takes backups, not while {self.state}."
            )
        self.store.request_stop(self.id)
        self._cancel_chain_task(self.chain[-1]["task_id"])
        latest_task_id = self.store.get(self.id).chain[-1]["task_id"]
        if latest_task_id != self.chain[-1]["task_id"]:  # the chain queued its next task meanwhile
            self._cancel_chain_task(latest_task_id)
        self.bench.audit_action("stop_migration", {"operation": self.id})

    def _cancel_chain_task(self, task_id: str) -> None:
        """Cancel the chain task; withdraw the stop when the task can no longer be cancelled."""
        try:
            self.bench.tasks.kill(task_id)
        except TaskNotRunningError:
            pass
        except TaskNotCancellableError as error:
            self.store.withdraw_stop(self.id)
            raise MigrationStateError("The update has started; it can no longer stop.") from error

    def finish_stop(self) -> None:
        """End a stopped migration: sites leave maintenance and the backups it took are deleted."""
        if self.state == "stopped":
            return
        if self.apps_updated or any(site.migration_status != "pending" for site in self.sites):
            raise MigrationStateError("The migration has changed the bench; it can no longer stop.")
        self._lift_maintenance()
        self._discard_snapshots()
        self.decisions.append({"action": "stop", "state": str(self.state), "at": utc_now()})
        self._transition("stopped")

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
        from pilot.internal.command import run_command

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
            raise BenchError("Restore is unavailable: nothing changed, or a migrated site has no backup.")
        self.diagnosis = None
        self._advance_revert()

    def next_revert_site(self) -> str | None:
        """The next site to restore from its backup."""
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
        """Check out each app's previous revision."""
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
        """Restore one site from its migration backup."""
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
        """Restart the services and discard the backups."""
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
        """The next revert phase that has work left."""
        if self.apps and not self.revert_checkpoints.get("apps"):
            return "reverting_apps"
        if self.next_revert_site() is not None:
            return "reverting_sites"
        return "restarting"

    def _fail_revert(self, error: Exception) -> None:
        """Record the error and move to revert_failed."""
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
