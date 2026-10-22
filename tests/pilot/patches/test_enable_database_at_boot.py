from __future__ import annotations

from pathlib import Path

from pilot.managers.database import MariaDBManager, PostgresManager
from pilot.patches.enable_database_at_boot import run

_TOML = """[bench]
name = "{name}"
python = "3.14"
db_type = "{db_type}"
"""


def _bench(root: Path, name: str, db_type: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "bench.toml").write_text(_TOML.format(name=name, db_type=db_type))


def test_benches_enable_their_database_unit_once(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("pilot.managers.platform.is_macos", lambda: False)
    monkeypatch.setattr(MariaDBManager, "unit_path", property(lambda self: tmp_path))
    monkeypatch.setattr(PostgresManager, "unit_path", property(lambda self: tmp_path / "missing"))
    monkeypatch.setattr(MariaDBManager, "enable_and_start", lambda self: calls.append("mariadb"))
    monkeypatch.setattr(PostgresManager, "enable_and_start", lambda self: calls.append("postgres"))
    root = tmp_path / "benches"
    _bench(root, "one", "mariadb")
    _bench(root, "two", "postgres")

    run(root)
    run(root)

    assert calls == ["mariadb"]
