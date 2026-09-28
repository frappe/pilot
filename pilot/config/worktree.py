import re
from dataclasses import dataclass

from pilot.exceptions import ConfigError

_NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,39}$")
_PORT_MAX = 65535


@dataclass
class WorktreeConfig:
    """One app worktree. Its ports derive from `port_offset`; its branch is read from git."""

    name: str
    app: str
    base_site: str
    port_offset: int

    @classmethod
    def from_dict(cls, data: dict) -> "WorktreeConfig":
        return cls(
            name=data.get("name", ""),
            app=data.get("app", ""),
            base_site=data.get("base_site", ""),
            port_offset=data.get("port_offset", 0),
        )

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "app": self.app,
            "base_site": self.base_site,
            "port_offset": self.port_offset,
        }

    def validate(self) -> None:
        if not isinstance(self.name, str) or not _NAME_PATTERN.match(self.name):
            raise ConfigError(
                f"Worktree name '{self.name}' is invalid. Use 1-40 lowercase letters, digits, "
                f"or '-', starting with a letter or digit."
            )
        if not self.app or not self.base_site:
            raise ConfigError(f"Worktree '{self.name}' must have app and base_site.")
        offset = self.port_offset
        if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
            raise ConfigError(f"Worktree '{self.name}': port_offset must be a non-negative integer.")
        from pilot.config.bench import BenchConfig

        if max(BenchConfig.default_ports().values()) + offset > _PORT_MAX:
            raise ConfigError(f"Worktree '{self.name}': port_offset {offset} puts its ports out of range.")
