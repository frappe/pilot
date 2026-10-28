from __future__ import annotations

import json
from dataclasses import dataclass
from typing import ClassVar

from pilot.core.database.configurations import DatabaseConfigurations
from pilot.exceptions import DatabaseError
from pilot.tasks import Task, step


@dataclass(kw_only=True)
class SetMariaDBConfigurationTask(Task):
    command: ClassVar[str] = "set-mariadb-configuration"
    timeout_seconds: ClassVar[int] = 300
    is_cancellable_while_running: ClassVar[bool] = False
    _AUDIT_ARG_KEYS: ClassVar[tuple[str, ...]] = ("variable", "value_json")

    variable: str
    value_json: str

    @step("configure", lambda self: f"Updating MariaDB variable {self.variable}")
    def run(self) -> None:
        try:
            value = json.loads(self.value_json)
        except (TypeError, json.JSONDecodeError) as exc:
            raise DatabaseError("The MariaDB configuration value is invalid.") from exc
        DatabaseConfigurations(self.bench.config).set(self.variable, value)


if __name__ == "__main__":
    SetMariaDBConfigurationTask.main()
