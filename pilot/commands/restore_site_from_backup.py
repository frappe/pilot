from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class NewSiteFromBackupCommand:
    def __init__(
        self,
        bench: "Bench",
        name: str,
        db_file: str,
        admin_password: str = "admin",
        public_files: str | None = None,
        private_files: str | None = None,
    ) -> None:
        self.bench = bench
        self.name = name
        self.db_file = db_file
        self.admin_password = admin_password
        self.public_files = public_files
        self.private_files = private_files

    def run(self) -> None:
        from pilot.commands.new_site import NewSiteCommand
        from pilot.config.site_config import SiteConfig
        from pilot.core.site import Site

        NewSiteCommand(self.bench, self.name, [], self.admin_password).run()
        print(f"Restoring backup: {self.db_file}")
        sys.stdout.flush()
        site = Site(SiteConfig(name=self.name, apps=[]), self.bench)
        site.restore(self.db_file, self.public_files, self.private_files)
