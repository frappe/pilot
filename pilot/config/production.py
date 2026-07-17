from dataclasses import dataclass

from pilot.exceptions import ConfigError

# Process managers usable in production. "none" is no longer a manager - an
# undeployed bench has production.enabled = false and an empty process_manager.
VALID_PROCESS_MANAGERS = ("systemd", "supervisor")


@dataclass
class ProductionConfig:
    enabled: bool = False
    process_manager: str = ""  # systemd | supervisor — required when enabled
    use_companion_manager: bool = False

    @classmethod
    def from_dict(cls, data: dict | None) -> "ProductionConfig":
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
        return cls(
            enabled=enabled,
            process_manager=pm,
            use_companion_manager=data.get("use_companion_manager", False),
        )

    @staticmethod
    def _normalize_process_manager(value: str) -> str:
        v = (value or "").strip().lower()
        if v in ("", "none"):
            return ""
        if v == "supervisord":
            return "supervisor"
        return v

    def validate(self, bench_name: str) -> None:
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
