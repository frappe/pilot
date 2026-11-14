import re
from dataclasses import dataclass, field
from pathlib import Path

from pilot.exceptions import ConfigError


@dataclass
class LetsEncryptConfig:
    email_pattern = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

    email: str = ""
    webroot_path: Path = field(default_factory=lambda: Path("/var/www/letsencrypt"))

    @classmethod
    def from_dict(cls, data: dict) -> "LetsEncryptConfig":
        """The Let's Encrypt config from its TOML table."""
        return cls(
            email=data.get("email", ""),
            webroot_path=Path(data.get("webroot_path", "/var/www/letsencrypt")),
        )

    def validate(self) -> None:
        """Raise when the email is invalid."""
        if self.email and not self.email_pattern.match(self.email):
            raise ConfigError(f"letsencrypt.email '{self.email}' is not a valid email address.")
