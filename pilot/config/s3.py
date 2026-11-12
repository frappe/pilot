from dataclasses import dataclass


@dataclass
class S3Config:
    """The bench bucket for site backups and, unless `upload_migration_backups` is off, migration recovery backups."""

    access_key: str = ""
    secret_key: str = ""
    bucket: str = ""
    provider: str = ""
    region: str = ""
    endpoint_url: str = ""
    upload_migration_backups: bool = True

    @property
    def stores_migration_backups(self) -> bool:
        """Whether migration backups go to S3."""
        return self.is_configured and self.upload_migration_backups

    @property
    def is_configured(self) -> bool:
        """Whether every S3 setting is set."""
        return all([self.access_key, self.secret_key, self.bucket, self.provider, self.region])
