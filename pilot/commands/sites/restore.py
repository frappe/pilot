from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class NewSiteFromBackupCommand:
    def __init__(
        self,
        bench: "Bench",
        name: str,
        db_file: str,
        admin_password: str,
        public_files: str | None = None,
        private_files: str | None = None,
    ) -> None:
        if not isinstance(admin_password, str) or not admin_password.strip():
            raise BenchError("Site Administrator password must not be empty.")
        self.bench = bench
        self.name = name
        self.db_file = db_file
        self.admin_password = admin_password
        self.public_files = public_files
        self.private_files = private_files

    def run(self) -> None:
        from pilot.commands.sites.create import NewSiteCommand
        from pilot.config.site_config import SiteConfig
        from pilot.core.site import Site

        # The site is created with (and restored into) the bench's single engine;
        # the backup must have been taken from a bench of that same engine.
        NewSiteCommand(self.bench, self.name, [], self.admin_password).run()
        print(f"Restoring backup: {self.db_file}")
        sys.stdout.flush()
        site = Site(SiteConfig(name=self.name, apps=[]), self.bench)
        site.restore(self.db_file, self.public_files, self.private_files)
