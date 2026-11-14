from __future__ import annotations

from pathlib import Path

from pilot.managers.database import MariaDBManager
from pilot.patches.write_mariadb_log_files import run

_TOML = """[bench]
name = "{name}"
python = "3.14"
db_type = "{db_type}"
"""


def _bench(root: Path, name: str, db_type: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "bench.toml").write_text(_TOML.format(name=name, db_type=db_type))


def test_the_shared_mariadb_log_files_are_set_once(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(MariaDBManager, "write_log_files", lambda self: calls.append("set"))
    root = tmp_path / "benches"
    _bench(root, "one", "mariadb")
    _bench(root, "two", "postgres")

    run(root)
    run(root)

    assert calls == ["set"]
