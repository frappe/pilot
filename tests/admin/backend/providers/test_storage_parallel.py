from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from admin.backend.providers import storage


def test_parallel_measuring_fills_the_cache_and_keeps_failures_for_the_real_read(tmp_path: Path) -> None:
    storage.directory_size_bytes.cache_clear()
    sizes = {str(tmp_path / "a"): 10, str(tmp_path / "b"): 20}

    def measure(path: str) -> int:
        if path not in sizes:
            raise OSError(path)
        return sizes[path]

    with patch.object(storage, "directory_bytes", side_effect=measure) as du:
        storage.measure_in_parallel([tmp_path / "a", tmp_path / "b", tmp_path / "gone"])
        assert du.call_count == 3
        assert storage.directory_size_bytes(str(tmp_path / "a")) == 10
        assert du.call_count == 3
        with pytest.raises(OSError):
            storage.directory_size_bytes(str(tmp_path / "gone"))
        assert du.call_count == 4
    storage.directory_size_bytes.cache_clear()
