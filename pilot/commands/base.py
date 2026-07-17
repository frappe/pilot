from __future__ import annotations

import argparse
from typing import TYPE_CHECKING, ClassVar, Optional

if TYPE_CHECKING:
    from pilot.core.bench import Bench


class Command:
    """Base class for self-registering CLI commands.

    A command owns everything about itself in one place: its CLI name, help
    text, arguments, and execution. The registry discovers every subclass that
    sets a ``name`` and wires it into the parser automatically — adding a
    command means creating one file, with no edits to cli.py or the registry.

    Subclasses keep their own ``__init__`` (used directly in tests and by other
    commands). The registry builds an instance via :meth:`from_args`, which maps
    the parsed argparse namespace onto that constructor.
    """

    #: CLI name, e.g. "remove-app". Subclasses without a name are not registered.
    name: ClassVar[str]
    #: One-line help shown in `bench --help`.
    help: ClassVar[str] = ""
    #: Parent group for subcommands, e.g. "setup" (None = top level).
    group: ClassVar[Optional[str]] = None
    #: If True, the registry loads the selected Bench and passes it to from_args.
    requires_bench: ClassVar[bool] = True
    #: If True, pass the Bench when one resolves, else None (ignored if requires_bench).
    optional_bench: ClassVar[bool] = False
    #: If True, require -b/--bench or running inside the bench dir (no auto-pick).
    requires_explicit_bench: ClassVar[bool] = False
    #: If True, `-b all` runs this command once per production-managed bench.
    supports_all_benches: ClassVar[bool] = False

    def __init__(self, bench: "Bench | None" = None) -> None:
        self.bench = bench

    @classmethod
    def add_arguments(cls, parser: argparse.ArgumentParser) -> None:
        """Declare this command's argparse arguments. Override as needed."""

    @classmethod
    def from_args(cls, args: argparse.Namespace, bench: "Bench | None") -> "Command":
        """Build an instance from parsed args. Default: a bench-only constructor."""
        return cls(bench)

    def run(self) -> None:
        raise NotImplementedError
