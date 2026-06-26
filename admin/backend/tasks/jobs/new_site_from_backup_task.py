from __future__ import annotations

from pilot.commands.restore_site_from_backup import NewSiteFromBackupCommand

from .base_task import BaseTask


class NewSiteFromBackupTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("name")
        p.add_argument("db_file")
        p.add_argument("--admin-password", default="admin")
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
        NewSiteFromBackupCommand(
            self.bench,
            self.name,
            self.db_file,
            admin_password=self.admin_password,
            public_files=self.public_files,
            private_files=self.private_files,
        ).run()


if __name__ == "__main__":
    NewSiteFromBackupTask.main()
