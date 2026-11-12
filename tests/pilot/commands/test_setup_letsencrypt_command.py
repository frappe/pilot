from __future__ import annotations

from pathlib import Path

from pilot.commands.setup.letsencrypt import SetupLetsEncryptCommand
from pilot.config import BenchConfig
from pilot.core.bench import Bench


def _make_bench(tmp_path: Path) -> Bench:
    bench_dir = tmp_path / "benches" / "prod"
    (bench_dir / "sites").mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text(
        '[bench]\nname = "prod"\npython = "3.14"\n\n'
        '[[apps]]\nname = "frappe"\nrepo = "r"\nbranch = "develop"\n\n'
        '[mariadb]\nroot_password = "root"\n\n'
        '[admin]\ndomain = "admin-prod.example.com"\ntls = false\n\n'
        '[production]\nenabled = true\nprocess_manager = "systemd"\n'
    )
    return Bench(BenchConfig.from_file(bench_dir / "bench.toml"), bench_dir)


def test_says_why_nothing_is_done_without_admin_tls(tmp_path: Path, capsys) -> None:
    SetupLetsEncryptCommand(_make_bench(tmp_path)).run()

    out = capsys.readouterr().out
    assert "No certificates to obtain" in out
    assert "--tls" in out
