from dataclasses import dataclass
from typing import ClassVar

from pilot.config import BenchConfig
from pilot.exceptions import BenchError
from pilot.tasks import Task, on_cancel, on_failure, step


@dataclass(kw_only=True)
class SetupLetsEncryptTask(Task):
    command: ClassVar[str] = "setup-letsencrypt"

    site: str = ""
    email: str = ""

    def run(self) -> None:
        """Issue the certificates."""
        self.setup_letsencrypt()

    @on_failure
    @on_cancel
    def disable_site_ssl(self) -> dict | None:
        """The callback args that turn site SSL off, if a site is set."""
        if not self.site:
            return None
        return {"site": self.site}

    @step("letsencrypt", "Set up Let's Encrypt")
    def setup_letsencrypt(self) -> None:
        """Save the email, turn on site TLS and issue the certificates."""
        self.require_production_privileges()
        self.apply_email()
        self.enable_site_tls()
        self.bench.setup_letsencrypt()
        self.require_site_certificate()

    def apply_email(self) -> None:
        """Save the Let's Encrypt email."""
        if not self.email:
            return
        with BenchConfig.open(self.bench_root) as config:
            config.letsencrypt.email = self.email
        self.bench.config.letsencrypt.email = self.email

    def enable_site_tls(self) -> None:
        """Turn SSL on for the site."""
        if not self.site:
            return
        self.bench.site(self.site).set_ssl(True)

    def require_site_certificate(self) -> None:
        """Fail when the site still has no certificate, so the failure callback turns
        its TLS off and the action can be retried."""
        from pilot.managers.letsencrypt import certificate_domains
        from pilot.managers.nginx import NginxManager

        if not self.site:
            return
        config = next(site.config for site in self.bench.sites() if site.config.name == self.site)
        served = NginxManager(self.bench).serviceable_tls_domains(config, ssl_ready=True)
        if missing := sorted(set(certificate_domains(config)) - set(served)):
            raise BenchError(
                f"No certificate covers {', '.join(missing)} yet. "
                "Point the DNS to this server, then enable SSL again."
            )


if __name__ == "__main__":
    SetupLetsEncryptTask.main()
