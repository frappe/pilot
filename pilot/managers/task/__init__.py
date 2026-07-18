from __future__ import annotations

from importlib import import_module

__all__ = [
    "TaskActivityReader",
    "TaskReader",
    "TaskStatus",
    "TaskWorkerControl",
    "sse_message",
    "task_has_secrets",
]

_EXPORTS = {
    "TaskActivityReader": ("pilot.managers.task.activity", "TaskActivityReader"),
    "TaskReader": ("pilot.managers.task.reader", "TaskReader"),
    "TaskStatus": ("pilot.managers.task.models", "TaskStatus"),
    "TaskWorkerControl": ("pilot.managers.task.control", "TaskWorkerControl"),
    "sse_message": ("pilot.managers.task.reader", "sse_message"),
    "task_has_secrets": ("pilot.managers.task.policy", "task_has_secrets"),
}


def __getattr__(name: str):
    if name not in _EXPORTS:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = _EXPORTS[name]
    value = getattr(import_module(module_name), attr_name)
    globals()[name] = value
    return value
