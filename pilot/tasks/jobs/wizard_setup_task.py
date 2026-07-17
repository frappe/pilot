from __future__ import annotations

from pilot.tasks.jobs.base_task import BaseTask


class WizardSetupTask(BaseTask):
    """Initialize the bench — the only thing the setup wizard does for a plain
    dev bench; production is a deliberate, separate step the user runs from the
    terminal afterwards (`bench setup production --admin-domain ... --tls`).

    A bench created from the admin UI's "New Bench" dialog is the exception: it
    arrives here with production.process_manager already chosen (the admin
    domain and TLS choice are on bench.toml too), but production.enabled still
    false — that flow only brings up the admin, since the workload, nginx,
    and TLS all depend on the venv/framework app this task's init step
    installs. Finish the same way `bench setup production` would once init has
    made that possible, instead of duplicating pieces of it here.
    """

    def run(self) -> None:
        self._step("init", "Initialize bench")
        self.bench.initialize(on_progress=self._report)
        if self.bench.config.production.process_manager:
            self._step("production", "Set up production")
            # A cert that can't issue yet (DNS still propagating for a domain
            # created moments ago) shouldn't roll back an otherwise-working
            # deployment - unlike a CLI `--tls` request, nobody's watching this to
            # retry by hand, so leave the bench live on HTTP and let it retry later.
            self.bench.setup_production(best_effort_tls=True, on_progress=self._report)
        self._step("done")


if __name__ == "__main__":
    WizardSetupTask.main()
