from dataclasses import dataclass

from pilot.exceptions import ConfigError

# Process managers usable in production. "none" is no longer a manager - an
# undeployed bench has production.enabled = false and an empty process_manager.
VALID_PROCESS_MANAGERS = ("systemd", "supervisor")


@dataclass
class ProductionConfig:
    enabled: bool = False
    process_manager: str = ""  # systemd | supervisor - required when enabled
    sandbox: bool | None = None  # None: not set, so on only with systemd; explicit true or false wins

    @classmethod
    def from_dict(cls, data: dict | None) -> "ProductionConfig":
        """The production config from its TOML table."""
        if data is None:
            return cls()
        pm = cls._normalize_process_manager(str(data.get("process_manager", "")))
        if "enabled" in data:
            enabled = bool(data.get("enabled"))
        else:
            # Legacy: presence of a real process_manager implied production.
            enabled = pm != ""
        # Oldest format derived the manager from a `lightweight` flag.
        if enabled and not pm and "lightweight" in data:
            pm = "systemd" if data.get("lightweight", False) else "supervisor"
        sandbox = data.get("sandbox")
        if sandbox is not None and not isinstance(sandbox, bool):
            raise ConfigError(f"production.sandbox must be true or false, got {sandbox!r}.")
        return cls(enabled=enabled, process_manager=pm, sandbox=sandbox)

    @property
    def is_sandboxed(self) -> bool:
        """Whether workload units run in the sandbox: the setting, else on for systemd."""
        return self.process_manager == "systemd" if self.sandbox is None else self.sandbox

    @staticmethod
    def _normalize_process_manager(value: str) -> str:
        """The process manager name in its standard form."""
        v = (value or "").strip().lower()
        if v in ("", "none"):
            return ""
        if v == "supervisord":
            return "supervisor"
        return v

    def validate(self, bench_name: str) -> None:
        """Raise when the production config is invalid."""
        pm = self.process_manager
        if self.enabled:
            if pm not in VALID_PROCESS_MANAGERS:
                raise ConfigError(
                    f"production.process_manager must be one of {', '.join(VALID_PROCESS_MANAGERS)} "
                    f"when production is enabled (bench '{bench_name}'), got '{pm or '(empty)'}'."
                )
        elif pm and pm not in VALID_PROCESS_MANAGERS:
            raise ConfigError(
                f"production.process_manager '{pm}' is invalid (bench '{bench_name}'). Must be one of {', '.join(VALID_PROCESS_MANAGERS)}."
            )
        # An empty manager is a bench not yet set up for production; setup picks the manager later.
        if self.sandbox and pm not in ("", "systemd"):
            raise ConfigError(
                f"production.sandbox needs process_manager = \"systemd\" (bench '{bench_name}'), got '{pm}'."
            )
