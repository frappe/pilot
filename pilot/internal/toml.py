from __future__ import annotations

import math
import re
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Any, Generic, TypeVar

ConfigT = TypeVar("ConfigT")
ConfigDict = dict[str, Any]

_BARE_KEY = re.compile(r"^[A-Za-z0-9_-]+$")
_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\t": "\\t",
    "\n": "\\n",
    "\f": "\\f",
    "\r": "\\r",
}


class Toml:
    def __init__(self) -> None:
        self._lines: list[str] = []

    @staticmethod
    def loads(value: str) -> ConfigDict:
        return tomllib.loads(value)

    @classmethod
    def dumps(cls, data: Mapping[str, Any]) -> str:
        writer = cls()
        writer._write_table(data, ())
        content = "\n".join(writer._lines) + ("\n" if writer._lines else "")
        cls.loads(content)
        return content

    def _write_table(self, data: Mapping[str, Any], path: tuple[str, ...]) -> None:
        scalars, tables, arrays = self._partition(data)
        self._write_scalars(scalars)
        for key, table in tables:
            self._write_header((*path, key), array=False)
            self._write_table(table, (*path, key))
        for key, entries in arrays:
            for entry in entries:
                self._write_header((*path, key), array=True)
                self._write_table(entry, (*path, key))

    def _partition(self, data: Mapping[str, Any]):
        scalars = []
        tables = []
        arrays = []
        for key, value in data.items():
            self._validate_key(key)
            if isinstance(value, Mapping):
                tables.append((key, value))
            elif self._is_array_of_tables(value):
                arrays.append((key, value))
            else:
                scalars.append((key, value))
        return scalars, tables, arrays

    def _write_scalars(self, values: list[tuple[str, Any]]) -> None:
        for key, value in values:
            self._lines.append(f"{self._key(key)} = {self._value(value)}")

    def _write_header(self, path: tuple[str, ...], *, array: bool) -> None:
        if self._lines and self._lines[-1] != "":
            self._lines.append("")
        name = ".".join(self._key(part) for part in path)
        self._lines.append(f"[[{name}]]" if array else f"[{name}]")

    def _value(self, value: Any) -> str:
        if isinstance(value, str):
            return self._string(value)
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, float):
            return self._float(value)
        if isinstance(value, time):
            return self._time(value)
        if isinstance(value, list):
            return self._array(value)
        if isinstance(value, (int, datetime, date)):
            return self._scalar(value)
        raise TypeError(f"Unsupported TOML value: {type(value).__name__}")

    @staticmethod
    def _scalar(value: int | datetime | date) -> str:
        return str(value) if isinstance(value, int) else value.isoformat()

    def _array(self, values: list[Any]) -> str:
        if any(isinstance(value, Mapping) for value in values):
            raise TypeError("TOML table arrays must contain only tables")
        return "[" + ", ".join(self._value(value) for value in values) + "]"

    @staticmethod
    def _float(value: float) -> str:
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return repr(value)

    @staticmethod
    def _time(value: time) -> str:
        if value.utcoffset() is not None:
            raise TypeError("TOML local times cannot have a UTC offset")
        return value.isoformat()

    @classmethod
    def _key(cls, value: str) -> str:
        return value if _BARE_KEY.fullmatch(value) else cls._string(value)

    @classmethod
    def _string(cls, value: str) -> str:
        return '"' + "".join(cls._escaped_character(character) for character in value) + '"'

    @staticmethod
    def _escaped_character(character: str) -> str:
        if character in _ESCAPES:
            return _ESCAPES[character]
        codepoint = ord(character)
        if 0xD800 <= codepoint <= 0xDFFF:
            raise ValueError("TOML strings cannot contain Unicode surrogate code points")
        if codepoint <= 0x1F or codepoint == 0x7F:
            return f"\\u{codepoint:04X}"
        return character

    @staticmethod
    def _is_array_of_tables(value: Any) -> bool:
        if not isinstance(value, list) or not value:
            return False
        has_table = any(isinstance(item, Mapping) for item in value)
        if has_table and not all(isinstance(item, Mapping) for item in value):
            raise TypeError("TOML table arrays cannot mix tables and scalar values")
        return has_table

    @staticmethod
    def _validate_key(value: Any) -> None:
        if not isinstance(value, str):
            raise TypeError(f"TOML keys must be strings, got {type(value).__name__}")


@dataclass(frozen=True)
class TomlDataclassCodec(Generic[ConfigT]):
    from_config_dict: Callable[[ConfigDict], ConfigT]
    to_config_dict: Callable[[ConfigT], ConfigDict]

    def from_dict(self, data: ConfigDict) -> ConfigT:
        return self.from_config_dict(data)

    def to_dict(self, config: ConfigT) -> ConfigDict:
        return self.to_config_dict(config)

    def loads(self, value: str) -> ConfigT:
        return self.from_dict(Toml.loads(value))

    def dumps(self, config: ConfigT) -> str:
        return Toml.dumps(self.to_dict(config))
