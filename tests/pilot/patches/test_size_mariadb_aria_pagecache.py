from __future__ import annotations

from pathlib import Path

from pilot.managers.database import MariaDBManager
from pilot.patches.size_mariadb_aria_pagecache import run

_TOML = """[bench]
name = "{name}"
python = "3.14"
db_type = "{db_type}"
"""


def _bench(root: Path, name: str, db_type: str) -> None:
    (root / name).mkdir(parents=True)
    (root / name / "bench.toml").write_text(_TOML.format(name=name, db_type=db_type))


def test_the_shared_mariadb_is_sized_once(tmp_path: Path, monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(MariaDBManager, "size_aria_pagecache", lambda self: calls.append("sized"))
    root = tmp_path / "benches"
    _bench(root, "one", "mariadb")
    _bench(root, "two", "postgres")

    run(root)
    run(root)

    assert calls == ["sized"]
