from dataclasses import dataclass

DEFAULT_URL = "https://cloud.frappe.io"


@dataclass
class FrappeCloudConfig:
    """The Frappe Cloud that sites are restored from."""

    url: str = DEFAULT_URL

    @classmethod
    def from_dict(cls, data: dict) -> "FrappeCloudConfig":
        """The Frappe Cloud config from its TOML table."""
        return cls(url=data.get("url") or DEFAULT_URL)
