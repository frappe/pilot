from __future__ import annotations

from pathlib import Path

from pilot.managers.nginx import NginxManager
from pilot.patches.refuse_symlinks_in_nginx import run

_TOML = """[bench]
name = "{name}"
python = "3.14"
"""


def test_each_bench_gets_its_nginx_config_rewritten_once(tmp_path: Path, monkeypatch) -> None:
    rewritten: list[str] = []
    monkeypatch.setattr(
        NginxManager, "reload_for_site_change", lambda self: rewritten.append(self.bench.config.name)
    )
    for name in ("one", "two"):
        (tmp_path / name).mkdir()
        (tmp_path / name / "bench.toml").write_text(_TOML.format(name=name))
    (tmp_path / "not-a-bench").mkdir()

    run(tmp_path)
    run(tmp_path)

    assert rewritten == ["one", "two"]
