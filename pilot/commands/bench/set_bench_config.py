from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar

from pilot.commands import Arg, Command
from pilot.exceptions import BenchError


@dataclass(kw_only=True)
class SetBenchConfigCommand(Command):
    name: ClassVar[str] = "set-bench-config"
    help: ClassVar[str] = "Set a bench.toml value by key."

    key: Annotated[str, Arg(help="Setting key, e.g. admin_domain.", metavar="key")]
    value: Annotated[str, Arg(help="New value: true/false, a number, or a string.", metavar="value")]

    def run(self) -> None:
        """Set one bench config value."""
        from pilot.config import BenchConfig
        from pilot.config.bench import FLAT_KEYS

        if self.key not in FLAT_KEYS:
            raise BenchError(
                f"Unknown bench setting '{self.key}'. Valid keys: {', '.join(sorted(FLAT_KEYS))}."
            )
        BenchConfig.write_flat(self.bench.path, self.bench.config.name, {self.key: self.value})
        if self.bench.is_initialized:
            # A fresh bench gets its runtime configs from `pilot init`.
            self.bench.rebuild_runtime_config()
        self.report(f"Set {self.key} in bench.toml.")
