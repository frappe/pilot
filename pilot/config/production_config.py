from dataclasses import dataclass

# Process managers usable in production. "none" is no longer a manager — an
# undeployed bench has production.enabled = false and an empty process_manager.
# openrc is the Alpine counterpart of systemd (rc-service/supervise-daemon).
VALID_PROCESS_MANAGERS = ("systemd", "supervisor", "openrc")


@dataclass
class ProductionConfig:
    enabled: bool = False
    process_manager: str = ""  # systemd | supervisor | openrc — required when enabled
    use_companion_manager: bool = False
