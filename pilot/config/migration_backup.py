from dataclasses import dataclass

from pilot.exceptions import ConfigError


@dataclass
class MigrationBackupConfig:
    gzip_level: int = 6
    workers: int = 4

    @classmethod
    def from_dict(cls, data: dict) -> "MigrationBackupConfig":
        defaults = cls()
        return cls(
            gzip_level=data.get("gzip_level", defaults.gzip_level),
            workers=data.get("workers", defaults.workers),
        )

    def validate(self) -> None:
        if type(self.gzip_level) is not int or not 1 <= self.gzip_level <= 9:
            raise ConfigError("migration_backup.gzip_level must be an integer from 1 to 9")
        if type(self.workers) is not int or self.workers < 1:
            raise ConfigError("migration_backup.workers must be a positive integer")
