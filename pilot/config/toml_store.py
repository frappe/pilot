from __future__ import annotations

import tomllib
from pathlib import Path

from pilot.config.bench_config import BenchConfig
from pilot.config.serializer import build, default_ports, flatten, to_toml
from pilot.utils import write_toml


class BenchTomlStore:
    """Single entry point for reading and writing a bench's ``bench.toml``.

    Wraps the parsing (``tomllib``/``BenchConfig``) and serialisation
    (``serializer.to_toml``/``write_toml``) primitives so that every caller
    funnels through one object instead of touching the file directly.
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
        if validate:
            return BenchConfig.from_file(self.path)
        return BenchConfig._from_dict(self.read_raw())

    def read_raw(self) -> dict:
        """Parsed TOML as a plain dict, preserving every section as written."""
        with self.path.open("rb") as fh:
            return tomllib.load(fh)

    def read_flat(self) -> dict:
        """Wizard's flat-key settings dict (parse-only)."""
        return flatten(self.read(validate=False))

    def port_offset(self) -> int:
        """Port offset already baked into this bench.toml, from its http_port.
        Pass back into write_flat() so a rewrite keeps every port on its grid."""
        if not self.exists():
            return 0
        try:
            base = default_ports()["http_port"]
            return self.read_raw().get("bench", {}).get("http_port", base) - base
        except Exception:
            return 0

    def write(self, config: BenchConfig) -> None:
        self.path.write_text(to_toml(config))

    def write_flat(self, name: str, settings: dict, port_offset: int = 0) -> None:
        """Serialise the wizard's flat-key settings dict to bench.toml."""
        self.write(build(name, settings, port_offset=port_offset))

    def write_raw(self, data: dict) -> None:
        write_toml(self.path, data)
