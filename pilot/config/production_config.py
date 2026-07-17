from dataclasses import dataclass

# Process managers usable in production. "none" is no longer a manager — an
# undeployed bench has production.enabled = false and an empty process_manager.
VALID_PROCESS_MANAGERS = ("systemd", "supervisor")


@dataclass
class ProductionConfig:
    enabled: bool = False
    process_manager: str = ""  # systemd | supervisor — required when enabled
    use_companion_manager: bool = False
