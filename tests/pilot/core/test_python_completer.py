from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pilot.core.site.console import python_completer
from pilot.core.site.console.python_completer import MIN_AVAILABLE_MB, PythonCompleter
from pilot.exceptions import LowMemoryError


def _start(available_mb: int, can_read: bool = True) -> MagicMock:
    completer = PythonCompleter(MagicMock(python=Path("python"), sites_path=Path("sites")))
    with (
        patch.object(python_completer, "can_read_memory", return_value=can_read),
        patch.object(python_completer, "available_memory_mb", return_value=available_mb),
        patch.object(python_completer.subprocess, "Popen") as popen,
    ):
        completer._start()
    return popen


def test_helper_starts_with_enough_free_memory() -> None:
    assert _start(MIN_AVAILABLE_MB).called


def test_helper_refuses_to_start_on_low_memory() -> None:
    with pytest.raises(LowMemoryError):
        _start(MIN_AVAILABLE_MB - 1)


def test_helper_starts_where_memory_cannot_be_read() -> None:
    assert _start(0, can_read=False).called
