from __future__ import annotations

from pathlib import Path

from pilot.managers.nginx import NginxManager
from pilot.patches.refuse_symlinks_in_nginx import run

_TOML = """[bench]
name = "{name}"
python = "3.14"

[admin]
domain = "admin.{name}.example.com"

[production]
enabled = true
process_manager = "systemd"
{sandbox}
"""


def test_each_sandboxed_bench_gets_its_nginx_config_rewritten_once(tmp_path: Path, monkeypatch) -> None:
    """A bench without the sandbox keeps its config, so a storage link in sites/ keeps working."""
    rewritten: list[str] = []
    monkeypatch.setattr(
        NginxManager, "reload_for_site_change", lambda self: rewritten.append(self.bench.config.name)
    )
    for name, sandbox in (("one", ""), ("two", "sandbox = true"), ("off", "sandbox = false")):
        (tmp_path / name).mkdir()
        (tmp_path / name / "bench.toml").write_text(_TOML.format(name=name, sandbox=sandbox))
    (tmp_path / "not-a-bench").mkdir()

    run(tmp_path)
    run(tmp_path)

    assert rewritten == ["one", "two"]
