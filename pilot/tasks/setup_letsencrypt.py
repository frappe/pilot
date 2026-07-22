from dataclasses import dataclass
from typing import ClassVar

from pilot.config import BenchConfig
from pilot.tasks import Task, on_cancel, on_failure, step


@dataclass(kw_only=True)
class SetupLetsEncryptTask(Task):
    command: ClassVar[str] = "setup-letsencrypt"

    site: str = ""
    email: str = ""

    def run(self) -> None:
        self.setup_letsencrypt()

    @on_failure
    @on_cancel
    def disable_site_ssl(self) -> dict | None:
        if not self.site:
            return None
        return {"site": self.site}

    @step("letsencrypt", "Set up Let's Encrypt")
    def setup_letsencrypt(self) -> None:
        self.require_production_privileges()
        self.apply_email()
        self.enable_site_tls()
        self.bench.setup_letsencrypt()

    def apply_email(self) -> None:
        if not self.email:
            return
        with BenchConfig.open(self.bench_root) as config:
            config.letsencrypt.email = self.email
        self.bench.config.letsencrypt.email = self.email

    def enable_site_tls(self) -> None:
        if not self.site:
            return
        self.bench.site(self.site).set_ssl(True)


if __name__ == "__main__":
    SetupLetsEncryptTask.main()
