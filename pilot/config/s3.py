from dataclasses import dataclass


@dataclass
class S3Config:
    access_key: str = ""
    secret_key: str = ""
    bucket: str = ""
    provider: str = ""
    region: str = ""

    @property
    def is_configured(self) -> bool:
        return all([self.access_key, self.secret_key, self.bucket, self.provider, self.region])
