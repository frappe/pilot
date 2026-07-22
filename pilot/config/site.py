from dataclasses import dataclass, field


@dataclass
class SiteConfig:
    name: str
    apps: list[str]
    admin_password: str | None = None
    domains: list[str] = field(default_factory=list)
    ssl: bool = False
    default: bool = False
    primary_domain: str = ""

    @property
    def all_domains(self) -> list[str]:
        return [self.name, *self.domains]

    @property
    def primary(self) -> str:
        return self.primary_domain or self.name
