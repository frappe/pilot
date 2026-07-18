from __future__ import annotations

from importlib import import_module

__all__ = [
    "Arg",
    "Task",
    "TaskCallback",
    "TaskCallbacks",
    "TaskRunner",
    "TaskSubmission",
    "step",
]

_EXPORTS = {
    "Arg": ("pilot.tasks.base", "Arg"),
    "Task": ("pilot.tasks.base", "Task"),
    "step": ("pilot.tasks.base", "step"),
    "TaskCallback": ("pilot.tasks.callbacks", "TaskCallback"),
    "TaskCallbacks": ("pilot.tasks.callbacks", "TaskCallbacks"),
    "TaskRunner": ("pilot.tasks.base", "TaskRunner"),
    "TaskSubmission": ("pilot.tasks.base", "TaskSubmission"),
}


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
