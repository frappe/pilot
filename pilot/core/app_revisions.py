from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RevisionPin:
    """A fixed revision (tag or commit) an app should be checked out at.

    Keeps App's public methods decoupled from the shape of any particular
    source's data (e.g. the marketplace registry's raw target dicts) -
    callers translate their own data into this before calling into App.
    A branch is not a fixed revision, so it has no RevisionPin; pass None
    to mean "no pin, follow the tracked branch" instead.
    """

    kind: Literal["tag", "commit"]
    ref: str

    @classmethod
    def from_marketplace_target(cls, target: dict) -> "RevisionPin | None":
        """Build a pin from a registry target dict, or None for a branch target."""
        kind = target.get("target_type")
        if kind not in ("tag", "commit"):
            return None
        return cls(kind=kind, ref=target["target"])
