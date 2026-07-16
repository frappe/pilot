from __future__ import annotations

import copy
from contextlib import contextmanager
from dataclasses import asdict, fields
from pathlib import Path
from typing import Iterator

from pilot.config.host_config import HostConfig
from pilot.internal.atomic_file import (
    atomic_write_private_text,
    exclusive_file_lock,
    replace_private_text_locked,
)
from pilot.internal.toml import Toml


class HostTomlStore:
    """Single entry point for reading and writing the host.toml shared by every
    bench under one benches directory, replacing the old pattern of inferring
    shared state by scanning sibling bench.toml files.
    """

    FILENAME = "host.toml"

    def __init__(self, path: Path) -> None:
        self.path = path / self.FILENAME if path.is_dir() else path

    @classmethod
    def for_bench(cls, bench_path: Path) -> "HostTomlStore":
        return cls(Path(bench_path).parent / cls.FILENAME)

    def read(self) -> HostConfig:
        if not self.path.exists():
            return HostConfig()
        return self._decode(Toml.loads(self.path.read_text(encoding="utf-8")))

    def write(self, config: HostConfig) -> None:
        atomic_write_private_text(self.path, self._encode(config))

    @contextmanager
    def edit(self) -> Iterator[HostConfig]:
        """Lock, load, and commit one read-modify-write transaction."""
        with exclusive_file_lock(self.path):
            config = self.read()
            original = copy.deepcopy(config)
            yield config
            if config != original:
                replace_private_text_locked(self.path, self._encode(config))

    @staticmethod
    def _decode(data: dict) -> HostConfig:
        known = {field.name for field in fields(HostConfig)}
        return HostConfig(**{key: value for key, value in data.items() if key in known})

    @staticmethod
    def _encode(config: HostConfig) -> str:
        return Toml.dumps(asdict(config))
