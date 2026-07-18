from __future__ import annotations

from importlib import import_module

__all__ = ["Arg", "BenchMode", "Command"]

_EXPORTS = {
    "Arg": ("pilot.commands.base", "Arg"),
    "BenchMode": ("pilot.commands.base", "BenchMode"),
    "Command": ("pilot.commands.base", "Command"),
}


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
