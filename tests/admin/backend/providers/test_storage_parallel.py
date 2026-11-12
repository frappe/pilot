from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from admin.backend.providers.server import storage
from pilot.internal.host_resources import DiskUsage


def test_parallel_measuring_fills_the_cache_and_keeps_failures_for_the_real_read(tmp_path: Path) -> None:
    storage.directory_size_bytes.cache_clear()
    sizes = {str(tmp_path / "a"): 10, str(tmp_path / "b"): 20}

    measured = []

    def measure(usage: DiskUsage) -> int:
        measured.append(usage.path)
        if str(usage.path) not in sizes:
            raise OSError(usage.path)
        return sizes[str(usage.path)]

    with patch.object(DiskUsage, "bytes", property(measure)):
        storage.measure_in_parallel([tmp_path / "a", tmp_path / "b", tmp_path / "gone"])
        assert len(measured) == 3
        assert storage.directory_size_bytes(str(tmp_path / "a")) == 10
        assert len(measured) == 3
        with pytest.raises(OSError):
            storage.directory_size_bytes(str(tmp_path / "gone"))
        assert len(measured) == 4
    storage.directory_size_bytes.cache_clear()
