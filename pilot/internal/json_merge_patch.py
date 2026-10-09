from __future__ import annotations

from typing import Any


def apply_merge_patch(target: dict[str, Any], patch: dict[str, Any]) -> None:
    """Apply a JSON Merge Patch to `target` in place.

    Refs: https://www.rfc-editor.org/rfc/rfc7396
    """
    for key, value in patch.items():
        if value is None:
            target.pop(key, None)
        elif isinstance(value, dict):
            if not isinstance(target.get(key), dict):
                target[key] = {}
            apply_merge_patch(target[key], value)
        else:
            target[key] = value
