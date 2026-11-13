from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, ClassVar, Literal

from pilot.commands import Arg, BenchMode, Command


@dataclass(kw_only=True)
class RunPatchesCommand(Command):
    name: ClassVar[str] = "run-patches"
    group: ClassVar[str] = "admin"
    help: ClassVar[str] = "Run pending Pilot upgrade patches (see pilot/patches/patches.txt)."
    bench_mode: ClassVar[BenchMode] = BenchMode.NONE

    phase: Annotated[
        Literal["pre_update", "post_update", "all"],
        Arg(help="Which patch phase to run."),
    ] = "all"

    def run(self) -> None:
        """Run the patches of one phase."""
        from pilot.internal.patch_runner import run_patches

        run_patches(self.phase, on_progress=self.report)
