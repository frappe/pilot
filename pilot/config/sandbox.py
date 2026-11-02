from dataclasses import asdict, dataclass, field
from pathlib import Path

from pilot.exceptions import ConfigError


@dataclass
class SandboxConfig:
    """systemd sandboxing for the bench's workload units, toggled per directive group."""

    enabled: bool = False
    filesystem: bool = True
    syscalls: bool = True
    kernel: bool = True
    devices: bool = True
    namespaces: bool = True
    network: bool = True
    inaccessible_paths: list[str] = field(default_factory=list)
    writable_paths: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: dict | None) -> "SandboxConfig":
        if not data:
            return cls()
        defaults = cls()
        return cls(
            enabled=bool(data.get("enabled", defaults.enabled)),
            filesystem=bool(data.get("filesystem", defaults.filesystem)),
            syscalls=bool(data.get("syscalls", defaults.syscalls)),
            kernel=bool(data.get("kernel", defaults.kernel)),
            devices=bool(data.get("devices", defaults.devices)),
            namespaces=bool(data.get("namespaces", defaults.namespaces)),
            network=bool(data.get("network", defaults.network)),
            inaccessible_paths=[str(path) for path in data.get("inaccessible_paths", [])],
            writable_paths=[str(path) for path in data.get("writable_paths", [])],
        )

    def to_dict(self) -> dict:
        return asdict(self)

    def validate(self, process_manager: str, bench_name: str) -> None:
        if self.enabled and process_manager != "systemd":
            raise ConfigError(
                f"production.sandbox needs process_manager = \"systemd\" (bench '{bench_name}'), "
                f"got '{process_manager or '(empty)'}'."
            )
        for key in ("inaccessible_paths", "writable_paths"):
            for path in getattr(self, key):
                if not Path(path).expanduser().is_absolute():
                    raise ConfigError(
                        f"production.sandbox.{key} entry '{path}' must be an absolute path or start with ~."
                    )
