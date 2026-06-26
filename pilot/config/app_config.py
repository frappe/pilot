from dataclasses import dataclass, field
from typing import List


@dataclass
class AppConfig:
    name: str
    repo: str
    branch: str
    branches: List[str] = field(default_factory=list)
