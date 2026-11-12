from __future__ import annotations

import copy
import re


class PublicConfig:
    """The part of a site or common site config that an Admin caller may read and patch.

    Protected keys stay hidden and cannot change. A top-level secret is write-only: it reads
    back as SECRET_MASK and may be set or removed. Nested secrets are hidden."""

    PROTECTED_KEYS = frozenset(
        {
            "backup_retention",
            # Pilot pins it on a rename; a site-scoped caller setting it could squat a
            # certificate lineage another site or a future one would need.
            "cert_name",
            "cloud_server_url",
            "cloud_settings_embed_url",
            "db_host",
            "db_name",
            "db_password",
            "db_port",
            "db_socket",
            "db_type",
            "db_user",
            "domains",
            "host_name",
            "installed_apps",
            "pilot_auth_token",
            "pilot_endpoint",
            "route",
            "ssl",
        }
    )
    SECRET_MASK = "********"
    SECRET_KEY_PARTS = (
        "_key",
        "access_key",
        "api_key",
        "authorization",
        "bearer",
        "cookie",
        "credential",
        "dsn",
        "password",
        "private_key",
        "secret",
        "session_id",
        "token",
    )
    # Matched after underscores are removed, to catch camelCase like apiKey.
    COMPACT_SECRET_KEY_PARTS = ("accesskey", "apikey", "encryptionkey", "privatekey", "secretkey")

    def __init__(self, config: dict) -> None:
        self.config = config

    @property
    def visible(self) -> dict:
        """The config as a caller may read it: protected keys left out, secrets masked."""
        return {
            key: self.SECRET_MASK if self.is_secret_key(key) else self.hide_nested_secrets(value)
            for key, value in self.config.items()
            if self.is_public_key(key) or self.is_secret_key(key)
        }

    def get_patch_error(self, patch) -> str | None:
        """Why a top-level patch is refused, or None when it may be applied."""
        if not isinstance(patch, dict):
            return "Configuration patches must be JSON objects."
        for key, value in patch.items():
            if isinstance(key, str) and self.is_secret_key(key):
                error = f"Enter the new value for {key}." if value == self.SECRET_MASK else None
            else:
                error = self.get_key_change_error(self.config, key, value)
            if error:
                return error
        return None

    def merge(self, patch: dict) -> dict:
        """A copy of the config with the patch applied. A None value removes the key."""
        merged = copy.deepcopy(self.config)
        for key, value in patch.items():
            current = self.config.get(key)
            if value is None:
                merged.pop(key, None)
            elif isinstance(value, dict) and isinstance(current, dict) and not self.is_secret_key(key):
                merged[key] = PublicConfig(current).merge(value)
            else:
                merged[key] = copy.deepcopy(value)
        return merged

    @classmethod
    def is_public_key(cls, key: str) -> bool:
        """Whether a caller may read and change this key."""
        normalized = cls.to_snake_case(key)
        compact = normalized.replace("_", "")
        return (
            normalized not in cls.PROTECTED_KEYS
            and normalized != "key"
            and not any(part in compact for part in cls.COMPACT_SECRET_KEY_PARTS)
            and not any(part in normalized for part in cls.SECRET_KEY_PARTS)
        )

    @classmethod
    def is_secret_key(cls, key: str) -> bool:
        """A secret-like key that is not system-managed: write-only at the top level."""
        return not cls.is_public_key(key) and cls.to_snake_case(key) not in cls.PROTECTED_KEYS

    @staticmethod
    def to_snake_case(key: str) -> str:
        """apiKey, api-key and API_KEY all become api_key."""
        return re.sub(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])", "_", key).lower().replace("-", "_")

    @classmethod
    def hide_nested_secrets(cls, value):
        """A deep copy of the value without any non-public key at any depth."""
        if isinstance(value, dict):
            return {
                key: cls.hide_nested_secrets(child) for key, child in value.items() if cls.is_public_key(key)
            }
        if isinstance(value, list):
            return [cls.hide_nested_secrets(item) for item in value]
        return copy.deepcopy(value)

    @classmethod
    def get_key_change_error(cls, current, key, value) -> str | None:
        """Why setting `key` to `value` inside `current` is refused, or None."""
        if not isinstance(key, str) or not cls.is_public_key(key):
            return "System-managed and secret-like configuration keys cannot be changed."
        existing = current.get(key) if isinstance(current, dict) else None
        if value is None:
            if cls.has_protected_keys(existing):
                return "A configuration value containing protected fields cannot be removed."
            return None
        if isinstance(value, dict):
            return cls.get_nested_patch_error(existing if isinstance(existing, dict) else {}, value)
        if isinstance(value, list):
            if cls.has_protected_keys(existing):
                return "A list containing protected fields cannot be replaced."
            return cls.get_new_value_error(value)
        if cls.has_protected_keys(existing):
            return "A configuration value containing protected fields cannot change type."
        return None

    @classmethod
    def get_nested_patch_error(cls, current, patch) -> str | None:
        """Why a patch for a nested object is refused, or None."""
        if not isinstance(patch, dict):
            return "Configuration patches must be JSON objects."
        for key, value in patch.items():
            if error := cls.get_key_change_error(current, key, value):
                return error
        return None

    @classmethod
    def get_new_value_error(cls, value) -> str | None:
        """Why a new list or object value is refused: it may not bring in a protected key."""
        if isinstance(value, dict):
            return cls.get_nested_patch_error({}, value)
        if isinstance(value, list):
            for item in value:
                if error := cls.get_new_value_error(item):
                    return error
        return None

    @classmethod
    def has_protected_keys(cls, value) -> bool:
        """Whether the value holds a non-public key at any depth."""
        if isinstance(value, dict):
            return any(
                not cls.is_public_key(key) or cls.has_protected_keys(child) for key, child in value.items()
            )
        if isinstance(value, list):
            return any(cls.has_protected_keys(child) for child in value)
        return False
