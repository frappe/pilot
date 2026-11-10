from __future__ import annotations

import tomllib
from pathlib import Path

from pilot.patches.set_sandbox_off_for_existing_benches import run

_TOML = """[bench]
name = "{name}"
python = "3.14"

[[apps]]
name = "frappe"
repo = "https://github.com/frappe/frappe"
branch = "version-16"

[production]
enabled = {production}
process_manager = "{manager}"
{sandbox}
[admin]
domain = "admin-{name}.example.com"
"""


def _bench(root: Path, name: str, sandbox: str = "", manager: str = "systemd") -> Path:
    (root / name).mkdir(parents=True)
    path = root / name / "bench.toml"
    path.write_text(_TOML.format(name=name, production="true", manager=manager, sandbox=sandbox))
    return path


def _sandbox(path: Path):
    return tomllib.loads(path.read_text())["production"].get("sandbox")


def test_a_bench_with_no_setting_is_pinned_to_false(tmp_path: Path) -> None:
    path = _bench(tmp_path, "one")

    run(tmp_path)

    assert _sandbox(path) is False


def test_a_bench_that_chose_a_value_keeps_it(tmp_path: Path) -> None:
    on = _bench(tmp_path, "on", sandbox="sandbox = true\n")
    off = _bench(tmp_path, "off", sandbox="sandbox = false\n")

    run(tmp_path)

    assert _sandbox(on) is True
    assert _sandbox(off) is False


def test_the_rest_of_the_file_is_left_as_it_was(tmp_path: Path) -> None:
    path = _bench(tmp_path, "one", manager="supervisor")
    before = tomllib.loads(path.read_text())

    run(tmp_path)

    after = tomllib.loads(path.read_text())
    after["production"].pop("sandbox")
    assert after == before


def test_a_folder_that_is_not_a_bench_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "common").mkdir()
    (tmp_path / "common_config.toml").write_text("")

    run(tmp_path)

    assert list((tmp_path / "common").iterdir()) == []


def test_the_patch_runs_once_per_bench(tmp_path: Path) -> None:
    path = _bench(tmp_path, "one")
    run(tmp_path)
    path.write_text(path.read_text().replace("sandbox = false\n", ""))

    run(tmp_path)

    assert _sandbox(path) is None
