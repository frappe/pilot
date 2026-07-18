from dataclasses import dataclass
from typing import ClassVar

from pilot.config.toml_store import BenchTomlStore
from pilot.tasks.base import BaseTask, step


@dataclass(kw_only=True)
class SetupLetsEncryptTask(BaseTask):
    command: ClassVar[str] = "setup-letsencrypt"

    site: str = ""
    email: str = ""

    def run(self) -> None:
        self.setup_letsencrypt()

    @step("letsencrypt", "Set up Let's Encrypt")
    def setup_letsencrypt(self) -> None:
        self.require_production_privileges()
        self.apply_email()
        self.enable_site_tls()
        self.bench.setup_letsencrypt()

    def apply_email(self) -> None:
        if not self.email:
            return
        with BenchTomlStore.for_bench(self.bench_root).edit() as config:
            config.letsencrypt.email = self.email
        self.bench.config.letsencrypt.email = self.email

    def enable_site_tls(self) -> None:
        if not self.site:
            return
        from pilot.core.site import Site

        Site.for_name(self.site, self.bench).set_ssl(True)


if __name__ == "__main__":
    SetupLetsEncryptTask.main()
