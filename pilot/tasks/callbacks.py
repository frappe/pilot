from __future__ import annotations

from typing import Literal, TypedDict


class TaskCallback(TypedDict):
    operation: str
    args: dict


class TaskCallbacks(TypedDict, total=False):
    on_success: TaskCallback | None
    on_failure: TaskCallback | None
    on_cancel: TaskCallback | None


TaskCallbackTrigger = Literal["on_success", "on_failure", "on_cancel"]


def on_success(func):
    return _callback_decorator("on_success", func)


def on_failure(func):
    return _callback_decorator("on_failure", func)


def on_cancel(func):
    return _callback_decorator("on_cancel", func)


def task_callbacks_for(task) -> TaskCallbacks:
    callbacks: TaskCallbacks = {}
    for method_name in dir(task):
        method = getattr(task, method_name)
        specs = getattr(method, "_task_callbacks", ())
        for trigger, operation in specs:
            args = method()
            if args is None:
                continue
            if trigger in callbacks:
                raise ValueError(f"Multiple {trigger} callbacks declared for {type(task).__name__}.")
            _set_callback(callbacks, trigger, {"operation": operation, "args": args})
    return callbacks


def _set_callback(callbacks: TaskCallbacks, trigger: TaskCallbackTrigger, callback: TaskCallback) -> None:
    if trigger == "on_success":
        callbacks["on_success"] = callback
    elif trigger == "on_failure":
        callbacks["on_failure"] = callback
    else:
        callbacks["on_cancel"] = callback


def _callback_decorator(trigger: TaskCallbackTrigger, func):
    callbacks = list(getattr(func, "_task_callbacks", ()))
    callbacks.append((trigger, func.__name__.replace("_", "-")))
    func._task_callbacks = tuple(callbacks)
    return func
