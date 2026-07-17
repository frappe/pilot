from __future__ import annotations

import copy
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pilot.internal.atomic_file import (
    atomic_write_private_text,
    exclusive_file_lock,
    replace_private_text_locked,
)
from pilot.config.bench_toml import dumps_config, load_config
from pilot.config.config_schema import preserve_unknown_config
from pilot.internal.toml import Toml
from pilot.config.bench import BenchConfig


class BenchTomlStore:
    """Single entry point for reading and writing a bench's ``bench.toml``.

    Wraps internal parsing and serialization so every caller funnels through
    one object instead of touching the file directly.
    """

    FILENAME = "bench.toml"

    def __init__(self, path: Path) -> None:
        # Accept either the bench directory or the bench.toml file itself.
        self.path = path / self.FILENAME if path.is_dir() else path

    @classmethod
    def for_bench(cls, bench_root: Path) -> "BenchTomlStore":
        return cls(Path(bench_root) / cls.FILENAME)

    def exists(self) -> bool:
        return self.path.exists()

    def read(self, validate: bool = True) -> BenchConfig:
        """Typed config. ``validate=False`` parses a half-configured file."""
        return load_config(self.path, validate=validate)

    def read_raw(self) -> dict:
        """Parsed TOML as a plain dict, preserving every section as written."""
        return Toml.loads(self.path.read_text(encoding="utf-8"))

    def read_flat(self) -> dict:
        """Wizard's flat-key settings dict (parse-only)."""
        from pilot.config.bench_toml_builder import BenchTomlBuilder

        return BenchTomlBuilder.read_settings(self.path)

    def write(self, config: BenchConfig) -> None:
        atomic_write_private_text(self.path, self._serialized_config(config))

    @contextmanager
    def edit(self) -> Iterator[BenchConfig]:
        """Lock, load, and commit one typed read-modify-write transaction."""
        with exclusive_file_lock(self.path):
            config = load_config(self.path)
            original = copy.deepcopy(config)
            yield config
            if config != original:
                replace_private_text_locked(self.path, self._serialized_config(config))

    @contextmanager
    def edit_raw(self) -> Iterator[dict]:
        """Lock, load, and commit one raw read-modify-write transaction."""
        with exclusive_file_lock(self.path):
            data = Toml.loads(self.path.read_text(encoding="utf-8"))
            original = copy.deepcopy(data)
            yield data
            if data != original:
                content = Toml.dumps(data)
                self._validate_serialized(content)
                replace_private_text_locked(self.path, content)

    def write_flat(self, name: str, settings: dict, port_offset: int = 0) -> None:
        """Atomically apply flat settings without replacing other TOML fields."""
        from pilot.config.bench_toml_builder import BenchTomlBuilder

        with exclusive_file_lock(self.path):
            if self.path.exists():
                original = Toml.loads(self.path.read_text(encoding="utf-8"))
                config = BenchConfig._from_dict(original)
                BenchTomlBuilder(name, settings).apply_to(config)
                replacement = Toml.loads(self._serialized_config(config))
                content = Toml.dumps(preserve_unknown_config(original, replacement))
                self._validate_serialized(content)
            else:
                config = BenchTomlBuilder(
                    name,
                    settings,
                    port_offset=port_offset,
                ).build()
                content = self._serialized_config(config)
            replace_private_text_locked(self.path, content)

    def write_raw(self, data: dict) -> None:
        content = Toml.dumps(data)
        self._validate_serialized(content)
        atomic_write_private_text(self.path, content)

    @staticmethod
    def _validate_serialized(content: str) -> None:
        config = BenchConfig._from_dict(Toml.loads(content))
        config.validate()

    @classmethod
    def _serialized_config(cls, config: BenchConfig) -> str:
        config.validate()
        content = dumps_config(config)
        cls._validate_serialized(content)
        return content
