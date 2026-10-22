from dataclasses import dataclass, field

from pilot.exceptions import ConfigError


@dataclass
class ProcessesConfig:
    disabled: list[str] = field(default_factory=list)  # app process names, such as mail-stalwart

    @classmethod
    def from_dict(cls, data: dict) -> "ProcessesConfig":
        return cls(disabled=data.get("disabled", []))

    def validate(self) -> None:
        if not isinstance(self.disabled, list) or not all(isinstance(name, str) for name in self.disabled):
            raise ConfigError("processes.disabled must be a list of process names.")
