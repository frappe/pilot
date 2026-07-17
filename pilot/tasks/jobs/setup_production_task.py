from pilot.tasks.jobs.base_task import BaseTask


class SetupProductionTask(BaseTask):
    def run(self) -> None:
        self._step("production", "Set up production")
        self.bench.setup_production(on_progress=self._report)
        self._step("done")


if __name__ == "__main__":
    SetupProductionTask.main()
