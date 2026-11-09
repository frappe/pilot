from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command
from pilot.exceptions import BenchError


def parse_value(raw: str) -> bool | int | float | str:
    """Infer a bench.toml value the way `bench set-config` does: booleans,
    then numbers, otherwise the string itself."""
    lowered = raw.lower()
    if lowered in ("true", "false"):
        return lowered == "true"
    try:
        return int(raw)
    except ValueError:
        pass
    try:
        return float(raw)
    except ValueError:
        return raw


@dataclass(kw_only=True)
class SetBenchConfigCommand(Command):
    name: ClassVar[str] = "set-bench-config"
    help: ClassVar[str] = "Set a bench.toml value by key."

    key: Annotated[str, Arg(help="Setting key, e.g. admin_domain.", metavar="key")]
    value: Annotated[str, Arg(help="New value: true/false, a number, or a string.", metavar="value")]

    def run(self) -> None:
        from pilot.config import BenchConfig
        from pilot.config.bench import FLAT_KEYS

        if self.key not in FLAT_KEYS:
            raise BenchError(
                f"Unknown bench setting '{self.key}'. Valid keys: {', '.join(sorted(FLAT_KEYS))}."
            )
        BenchConfig.write_flat(self.bench.path, self.bench.config.name, {self.key: parse_value(self.value)})
        if self.bench.is_initialized:
            # A fresh bench gets its runtime configs from `pilot init`.
            self.bench.rebuild_runtime_config()
        self.report(f"Set {self.key} in bench.toml.")
