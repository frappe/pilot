from __future__ import annotations

from pilot.core.site import provision_from_backup

from pilot.tasks.jobs.base_task import BaseTask


class NewSiteFromBackupTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("name")
        p.add_argument("db_file")
        p.set_defaults(admin_password=None)
        p.add_argument("--public-files", default=None)
        p.add_argument("--private-files", default=None)
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.name = args.name
        self.db_file = args.db_file
        self.admin_password = args.admin_password
        self.public_files = args.public_files
        self.private_files = args.private_files

    def run(self) -> None:
        self._require_production_privileges()
        self._step("restore", f"Restore site {self.name} from backup")
        provision_from_backup(
            self.bench,
            self.name,
            self.db_file,
            admin_password=self.admin_password,
            public_files=self.public_files,
            private_files=self.private_files,
            on_progress=self._report,
        )
        self._step("done")


if __name__ == "__main__":
    NewSiteFromBackupTask.main()
