from pilot.commands.new_site import NewSiteCommand
from .base_task import BaseTask


class NewSiteTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("name")
        p.add_argument("--admin-password", default="admin")
        p.add_argument("--db-type", default=None)
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.name = args.name
        self.admin_password = args.admin_password
        self.db_type = args.db_type

    def run(self) -> None:
        self._step("create", f"Create site {self.name}")
        NewSiteCommand(self.bench, self.name, [], self.admin_password, db_type=self.db_type).run()
        self._step("done")


if __name__ == "__main__":
    NewSiteTask.main()
