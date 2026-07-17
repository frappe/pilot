from dataclasses import dataclass


@dataclass
class PostgresConfig:
    host: str = "localhost"
    port: int = 5432
    root_password: str = ""
    admin_user: str = "postgres"
    existing: bool = False
