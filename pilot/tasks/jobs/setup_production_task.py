import argparse
from pathlib import Path

from pilot.core.bench import Bench
from pilot.tasks.jobs.base_task import BaseTask


class SetupProductionTask(BaseTask):
    def __init__(self, bench: Bench, bench_root: Path, args: argparse.Namespace) -> None:
        super().__init__(bench, bench_root, args)
        self.process_manager = args.process_manager
        self.admin_domain = args.admin_domain
        self.tls = args.tls
        self.letsencrypt_email = args.letsencrypt_email

    @classmethod
    def _parser(cls) -> argparse.ArgumentParser:
        p = super()._parser()
        p.add_argument("--process-manager")
        p.add_argument("--admin-domain")
        p.add_argument("--tls", action="store_true")
        p.add_argument("--letsencrypt-email")
        return p

    def run(self) -> None:
        self._step("production", "Set up production")
        self.bench.setup_production(
            process_manager=self.process_manager,
            admin_domain=self.admin_domain,
            admin_tls=self.tls,
            letsencrypt_email=self.letsencrypt_email,
            on_progress=self._report,
        )
        self._step("done")


if __name__ == "__main__":
    SetupProductionTask.main()
