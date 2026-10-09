from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, fields


@dataclass
class TomlSchema:
    """The keys, nested tables and arrays of tables that one TOML table may hold."""

    keys: set[str] = field(default_factory=set)
    tables: dict[str, TomlSchema] = field(default_factory=dict)
    arrays: dict[str, TomlSchema] = field(default_factory=dict)

    def is_known(self, key: str) -> bool:
        return key in self.keys or key in self.tables or key in self.arrays

    def get_unknown_paths(self, data: Mapping, prefix: str = "") -> list[str]:
        """Dotted paths of the keys in `data` that this schema does not declare."""
        unknown: list[str] = []
        for key, value in data.items():
            path = f"{prefix}{key}"
            if key in self.tables and isinstance(value, Mapping):
                unknown += self.tables[key].get_unknown_paths(value, f"{path}.")
            elif key in self.arrays and isinstance(value, list):
                for index, entry in enumerate(value):
                    if isinstance(entry, Mapping):
                        unknown += self.arrays[key].get_unknown_paths(entry, f"{path}[{index}].")
            elif not self.is_known(key):
                unknown.append(path)
        return unknown


def field_names(dataclass_type: type) -> set[str]:
    return {item.name for item in fields(dataclass_type)}
