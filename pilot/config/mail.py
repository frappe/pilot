from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from pilot.config.common_site_config import read_common_site_config, update_common_site_config
from pilot.exceptions import MalformedSiteConfig

ADDRESS_RE = re.compile(r"^[^@\s]+@[^@\s]+$")

STARTTLS_PORT = 587
SSL_PORT = 465

CONFIG_KEYS = (
    "mail_server",
    "mail_port",
    "mail_login",
    "mail_password",
    "auto_email_id",
    "use_tls",
    "disable_mail_smtp_authentication",
)


class MailEndpoint(NamedTuple):
    host: str
    port: int
    is_ssl: bool
    username: str
    sender: str


@dataclass
class MailConfig:
    """Outgoing mail for alerts, stored in the bench's common_site_config.json
    under the keys the framework's Email Account already reads."""

    server: str = ""
    port: int = 0
    email: str = ""
    login: str = ""
    password: str = ""
    use_ssl: bool = False

    @property
    def is_configured(self) -> bool:
        if not (self.server and self.email):
            return False
        try:
            self.get_endpoint()
        except ValueError:
            return False
        return True

    def get_endpoint(self) -> MailEndpoint:
        host = self.server.strip()
        if not host:
            raise ValueError("mail_server is required to send alert emails.")
        sender = self.email.strip()
        if not ADDRESS_RE.match(sender):
            raise ValueError("auto_email_id must be an email address.")
        port = self.port or (SSL_PORT if self.use_ssl else STARTTLS_PORT)
        if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
            raise ValueError("mail_port must be a port number between 1 and 65535.")
        return MailEndpoint(
            host=host,
            port=port,
            is_ssl=bool(self.use_ssl),
            username=(self.login.strip() or sender) if self.password else "",
            sender=sender,
        )

    def validate(self) -> None:
        if self.server:
            self.get_endpoint()

    @classmethod
    def read(cls, sites_path: Path) -> "MailConfig":
        """A damaged file reads as no mailbox rather than raising: the monitor
        tick calls this, and an unparsable file is not a reason to kill it."""
        try:
            config = read_common_site_config(sites_path)
        except MalformedSiteConfig:
            return cls()
        return cls(
            server=config.get("mail_server", ""),
            port=config.get("mail_port", 0) or 0,
            # The framework falls back to mail_login when auto_email_id is unset.
            email=config.get("auto_email_id") or config.get("mail_login", ""),
            login=config.get("mail_login", ""),
            password=config.get("mail_password", ""),
            use_ssl=not config.get("use_tls", 1) if config.get("mail_server") else False,
        )

    def write(self, sites_path: Path) -> None:
        """Clearing the server is the only way to drop a stored mailbox. Settings
        that merely fail to validate are left on disk rather than deleted, so a
        hand-edited file is never destroyed by an unrelated save."""
        if not (sites_path / "common_site_config.json").exists():
            return
        if self.server and not self.is_configured:
            read_common_site_config(sites_path)  # a damaged file still fails the save
            return
        endpoint = self.get_endpoint() if self.is_configured else None
        with update_common_site_config(sites_path) as config:
            for key in CONFIG_KEYS:
                config.pop(key, None)
            if endpoint is None:
                return
            config["mail_server"] = endpoint.host
            config["mail_port"] = endpoint.port
            config["auto_email_id"] = endpoint.sender
            config["use_tls"] = 0 if endpoint.is_ssl else 1
            if endpoint.username:
                config["mail_login"] = endpoint.username
                config["mail_password"] = self.password
            else:
                config["disable_mail_smtp_authentication"] = 1
