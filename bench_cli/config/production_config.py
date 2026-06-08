from dataclasses import dataclass

VALID_PROCESS_MANAGERS = ("none", "supervisor", "systemd")


@dataclass
class ProductionConfig:
    process_manager: str = "none"  # none | supervisor | systemd
    nginx: bool = False

    @property
    def enabled(self) -> bool:
        return self.process_manager != "none"
