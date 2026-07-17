"""Production process-manager backends and their shared machinery.

The dev foreground runner, ProcessDefinition, and the ProcessManager
constructors live in local.py. base.py holds the production side shared by
every backend (ManagedProcessManager + UnitGroup + ServiceRenderer); systemd.py
and supervisor.py are one module per backend.
"""

from __future__ import annotations

from pilot.managers.processes.base import ManagedProcessManager, UnitGroup

__all__ = ["ManagedProcessManager", "UnitGroup"]
