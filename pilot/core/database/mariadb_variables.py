from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

MariaDBValue = bool | int | str
VariableType = Literal["boolean", "integer", "string"]
VariableEditAction = Literal[
    "configuration",
    "innodb_buffer_pool_size",
    "max_connections",
    "performance_schema",
]

MARIADB_VARIABLES_PATH = Path(__file__).with_name("mariadb_variables.json")


@dataclass(frozen=True)
class MariaDBVariableSpec:
    """One MariaDB variable as defined in mariadb_variables.json."""

    name: str
    label: str
    section: str
    description: str
    value_type: VariableType = "string"
    dynamic: bool = False
    edit_action: VariableEditAction | None = None
    minimum: int | None = None
    maximum: int | None = None
    step: int | None = None
    unit: str = ""
    read_only_reason: str = ""

    @classmethod
    def from_definition(cls, definition: dict, reasons: dict[str, str]) -> MariaDBVariableSpec:
        """The variable spec from its JSON definition."""
        edit_action = definition.get("edit")
        reason = definition.get("reason", "" if edit_action else "startup")
        return cls(
            name=definition["name"],
            label=definition["label"],
            section=definition["section"],
            description=definition["description"],
            value_type=definition.get("type", "string"),
            dynamic=definition.get("dynamic", False),
            edit_action=edit_action,
            minimum=definition.get("min"),
            maximum=definition.get("max"),
            step=definition.get("step"),
            unit=definition.get("unit", ""),
            read_only_reason=reasons[reason] if reason else "",
        )

    @property
    def option_name(self) -> str:
        """The variable name as a config file option."""
        return self.name.replace("_", "-")

    @property
    def editable(self) -> bool:
        """Whether Pilot can change the variable."""
        return self.edit_action is not None


class MariaDBVariableValidator:
    """Parses server values and validates requested values for one variable."""

    def __init__(self, spec: MariaDBVariableSpec) -> None:
        self.spec = spec

    def parse_server_value(self, raw_value: object) -> MariaDBValue:
        """The typed value from the server's raw value."""
        if self.spec.value_type == "integer":
            try:
                return int(str(raw_value))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"MariaDB returned an invalid value for '{self.spec.name}'.") from exc
        if self.spec.value_type == "boolean":
            normalized = str(raw_value).strip().upper()
            if normalized in {"1", "ON", "TRUE"}:
                return True
            if normalized in {"0", "OFF", "FALSE"}:
                return False
            raise ValueError(f"MariaDB returned an invalid value for '{self.spec.name}'.")
        return str(raw_value)

    def validate_input(self, value: object) -> MariaDBValue:
        """The validated value from user input; raise when it is invalid."""
        self._require_generic_edit_action()
        if self.spec.value_type == "integer":
            return self._validate_integer(value)
        if self.spec.value_type == "boolean":
            if type(value) is not bool:
                raise ValueError(f"{self.spec.label} must be either enabled or disabled.")
            return value
        raise ValueError(f"MariaDB variable '{self.spec.name}' is not editable in Pilot.")

    def option_value(self, value: object) -> str:
        """The value as a config file option."""
        validated = self.validate_input(value)
        if type(validated) is bool:
            return "ON" if validated else "OFF"
        return str(validated)

    def _validate_integer(self, value: object) -> int:
        """The integer within the allowed range; raise otherwise."""
        spec = self.spec
        if type(value) is not int:
            raise ValueError(f"{spec.label} must be a whole number.")
        if spec.minimum is not None and value < spec.minimum:
            raise ValueError(f"{spec.label} must be at least {spec.minimum}{self._unit_suffix()}.")
        if spec.maximum is not None and value > spec.maximum:
            raise ValueError(f"{spec.label} cannot be greater than {spec.maximum}{self._unit_suffix()}.")
        return value

    def _require_generic_edit_action(self) -> None:
        """Raise unless the variable is edited through the config file."""
        if self.spec.edit_action is None:
            raise ValueError(f"MariaDB variable '{self.spec.name}' is read-only in Pilot.")
        if self.spec.edit_action != "configuration":
            raise ValueError(
                f"MariaDB variable '{self.spec.name}' must be changed through its guarded database action."
            )

    def _unit_suffix(self) -> str:
        """The unit for messages."""
        return f" {self.spec.unit}" if self.spec.unit else ""


def load_mariadb_variable_specs(path: Path = MARIADB_VARIABLES_PATH) -> tuple[MariaDBVariableSpec, ...]:
    """The variable specs from the JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    return tuple(MariaDBVariableSpec.from_definition(item, data["reasons"]) for item in data["variables"])


MARIADB_VARIABLE_SPECS = load_mariadb_variable_specs()
MARIADB_VARIABLES_BY_NAME = {spec.name: spec for spec in MARIADB_VARIABLE_SPECS}
MARIADB_VARIABLE_NAMES = frozenset(MARIADB_VARIABLES_BY_NAME)
EDITABLE_MARIADB_VARIABLE_NAMES = frozenset(spec.name for spec in MARIADB_VARIABLE_SPECS if spec.editable)
GENERIC_EDITABLE_MARIADB_VARIABLE_NAMES = frozenset(
    spec.name for spec in MARIADB_VARIABLE_SPECS if spec.edit_action == "configuration"
)


def mariadb_variable_spec(name: str) -> MariaDBVariableSpec:
    """The spec of a supported variable; raise for others."""
    try:
        return MARIADB_VARIABLES_BY_NAME[name]
    except KeyError as exc:
        raise ValueError(f"MariaDB variable '{name}' is not supported by Pilot.") from exc
