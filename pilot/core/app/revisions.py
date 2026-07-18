from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class RevisionPin:
    """A fixed app revision target: tag or commit, never a branch."""

    kind: Literal["tag", "commit"]
    ref: str

    @classmethod
    def from_marketplace_target(cls, target: dict) -> "RevisionPin | None":
        """Build a pin from a registry target dict, or None for a branch target."""
        kind = target.get("target_type")
        if kind not in ("tag", "commit"):
            return None
        return cls(kind=kind, ref=target["target"])
