from pilot.commands.new_site import NewSiteCommand
from .base_task import BaseTask


class NewSiteTask(BaseTask):
    @classmethod
    def _parser(cls):
        p = super()._parser()
        p.add_argument("name")
        p.add_argument("--admin-password", default="admin")
        return p

    def __init__(self, bench, bench_root, args):
        super().__init__(bench, bench_root, args)
        self.name = args.name
        self.admin_password = args.admin_password

    def run(self) -> None:
        NewSiteCommand(self.bench, self.name, [], self.admin_password).run()


if __name__ == "__main__":
    NewSiteTask.main()
