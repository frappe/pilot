from dataclasses import dataclass, field


@dataclass
class AppConfig:
    name: str
    repo: str
    branch: str
    branches: list[str] = field(default_factory=list)
    path: str = ""
    follow_branch: bool = False

    @property
    def is_linked(self) -> bool:
        return bool(self.path)
