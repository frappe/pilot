"""Production process-manager backends and their shared machinery.

The dev foreground runner, ProcessDefinition and the ProcessManager constructors
live one level up in bench_cli.managers.process_manager. This package holds only
the production side: base.py (ManagedProcessManager + UnitGroup), renderers.py (config
text builders), and one module per backend (systemd/supervisor/openrc).
"""

from __future__ import annotations

from bench_cli.managers.process_managers.base import ManagedProcessManager, UnitGroup

__all__ = ["ManagedProcessManager", "UnitGroup"]
