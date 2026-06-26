from dataclasses import dataclass, field
from typing import List


@dataclass
class SiteConfig:
    name: str
    apps: List[str]
    admin_password: str = "admin"
    domains: List[str] = field(default_factory=list)
    ssl: bool = False
    default: bool = False
    primary_domain: str = ""

    @property
    def all_domains(self) -> List[str]:
        return [self.name] + self.domains

    @property
    def primary(self) -> str:
        return self.primary_domain or self.name
