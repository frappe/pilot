from __future__ import annotations

from pathlib import Path

from pilot.patches.move_site_backups import run


def _bench(tmp_path: Path) -> Path:
    bench = tmp_path / "benches" / "one"
    (bench / "sites" / "a.localhost" / "private" / "backups").mkdir(parents=True)
    (bench / "bench.toml").write_text('[bench]\nname = "one"\n')
    return bench


def test_backup_runs_move_and_other_files_stay(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    old = bench / "sites" / "a.localhost" / "private" / "backups"
    (old / "20260101_020000-a_localhost-database.sql.gz").write_text("db")
    (old / "notes.txt").write_text("keep")

    run(tmp_path / "benches")

    new = bench / "sites" / "a.localhost" / "backups"
    assert (new / "20260101_020000-a_localhost-database.sql.gz").read_text() == "db"
    assert (old / "notes.txt").exists()
    assert not (old / "20260101_020000-a_localhost-database.sql.gz").exists()


def test_a_run_already_moved_is_not_overwritten(tmp_path: Path) -> None:
    bench = _bench(tmp_path)
    name = "20260101_020000-a_localhost-database.sql.gz"
    (bench / "sites" / "a.localhost" / "private" / "backups" / name).write_text("stale")
    (bench / "sites" / "a.localhost" / "backups").mkdir()
    (bench / "sites" / "a.localhost" / "backups" / name).write_text("moved")

    run(tmp_path / "benches")

    assert (bench / "sites" / "a.localhost" / "backups" / name).read_text() == "moved"
