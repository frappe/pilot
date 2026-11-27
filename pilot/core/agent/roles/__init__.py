from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

if TYPE_CHECKING:
    from pilot.config.common import CommonConfig


class AgentRole:
    """One kind of work a VM does for the VM that manages it, with its requests under `/{name}`.

    Each role builds its `router`, a FastAPI `APIRouter`, in `__init__`. This package imports no FastAPI, because
    the role clients in it run on the CLI's system Python.
    """

    name: ClassVar[str]

    @classmethod
    def is_enabled(cls, config: CommonConfig) -> bool:
        """Whether this VM has the role."""
        raise NotImplementedError

    def __init__(self, config: CommonConfig) -> None:
        self.config = config

    def get_health(self) -> dict:
        """The role's state, which the agent reports under its name in `/health`."""
        raise NotImplementedError
