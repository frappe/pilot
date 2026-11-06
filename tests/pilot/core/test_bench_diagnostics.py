import json
import os
import subprocess
import sys
import venv
from pathlib import Path

import pytest

from pilot.config import AppConfig, BenchConfig
from pilot.core.bench.diagnostics import BenchDiagnostics

REPO = Path(__file__).resolve().parents[3]


def run_doctor(path: Path | None, *arguments: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    command = [sys.executable, "-c", "from pilot.cli import main; main()", "doctor"]
    if path is not None:
        command.extend(["--path", str(path)])
    return subprocess.run(
        [*command, *arguments],
        cwd=cwd or REPO,
        env=dict(os.environ, PYTHONPATH=str(REPO)),
        text=True,
        capture_output=True,
        check=False,
    )


def create_checkout(path: Path, module: str) -> None:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "--quiet", str(path)], check=True)
    (path / module).mkdir()
    (path / module / "hooks.py").write_text("raise RuntimeError('Doctor must never import app code')\n")
    (path / "pyproject.toml").write_text(f'[project]\nname = "{module}"\n')


@pytest.fixture
def bench_path(tmp_path: Path) -> Path:
    root = tmp_path / "bench"
    root.mkdir()
    config = BenchConfig.from_flat("demo", {"db_type": "sqlite"})
    config.write(root)
    venv.EnvBuilder(with_pip=False).create(root / "env")
    create_checkout(root / "apps" / "frappe", "frappe")
    sites = root / "sites"
    sites.mkdir()
    (sites / "apps.txt").write_text("frappe\n")
    (sites / "common_site_config.json").write_text(json.dumps({"db_type": "sqlite"}))
    site = sites / "demo.localhost"
    site.mkdir()
    (site / "site_config.json").write_text(json.dumps({"db_name": "demo", "db_password": "never-print-this"}))
    return root


def snapshot(root: Path) -> dict:
    return {
        str(path.relative_to(root)): (
            path.lstat().st_mode,
            path.lstat().st_mtime_ns,
            path.read_bytes() if path.is_file() else None,
        )
        for path in root.rglob("*")
        if not path.is_symlink()
    }


def test_real_cli_reports_clean_sqlite_bench_without_mutating_files(bench_path: Path) -> None:
    before = snapshot(bench_path)

    result = run_doctor(bench_path, "--json")

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["schema_version"] == 1 and payload["ok"] is True
    assert {check["code"] for check in payload["checks"]} >= {
        "python",
        "app_checkout",
        "site_database_engine",
    }
    assert "never-print-this" not in result.stdout + result.stderr
    assert snapshot(bench_path) == before


def test_real_cli_exposes_breakage_and_recovers_after_files_are_repaired(bench_path: Path) -> None:
    config = bench_path / "sites" / "demo.localhost" / "site_config.json"
    original = config.read_bytes()
    config.write_text('{"db_password": "never-print-this", malformed')
    marker = bench_path / ".initializing"
    marker.touch()

    broken = run_doctor(bench_path)

    assert broken.returncode == 1
    assert "ERROR site_config" in broken.stdout
    assert "ERROR initialization" in broken.stdout
    assert "Fix:" in broken.stdout
    assert "never-print-this" not in broken.stdout + broken.stderr
    config.write_bytes(original)
    marker.unlink()
    repaired = run_doctor(bench_path, "--json")
    assert repaired.returncode == 0
    assert json.loads(repaired.stdout)["ok"] is True


def test_malformed_bench_toml_is_reported_without_echoing_credentials(bench_path: Path) -> None:
    (bench_path / "bench.toml").write_text('password = "secret-value"\ninvalid = [')

    result = run_doctor(bench_path, "--json")

    assert result.returncode == 1
    payload = json.loads(result.stdout)
    assert any(check["code"] == "bench_config" and check["status"] == "error" for check in payload["checks"])
    assert "secret-value" not in result.stdout + result.stderr
    assert any(check["code"] == "site_config" for check in payload["checks"])


def test_linked_git_checkout_and_rename_alias_are_supported(bench_path: Path, tmp_path: Path) -> None:
    source = tmp_path / "linked"
    create_checkout(source, "my_app")
    config = BenchConfig.read(bench_path)
    config.apps.append(AppConfig(name="my_app", repo="", branch="", path=str(source)))
    config.write(bench_path)
    link = bench_path / "apps" / "my_app"
    link.symlink_to(source, target_is_directory=True)
    (bench_path / "sites" / "apps.txt").write_text("frappe\nmy_app\n")
    (bench_path / "sites" / "old.localhost").symlink_to("demo.localhost", target_is_directory=True)

    clean = run_doctor(bench_path, "--json")

    assert clean.returncode == 0, clean.stdout + clean.stderr
    payload = json.loads(clean.stdout)
    assert sum(check["code"] == "site_config" for check in payload["checks"]) == 1
    source.rename(tmp_path / "moved")
    broken = run_doctor(bench_path, "--json")
    assert broken.returncode == 1
    assert any(
        check["code"] == "app_link" and check["status"] == "error"
        for check in json.loads(broken.stdout)["checks"]
    )


@pytest.mark.parametrize(
    ("shared_engine", "site_engine", "expected_status"),
    [
        ("postgres", None, "error"),
        ("postgres", "sqlite", "ok"),
        ("sqlite", "mariadb", "error"),
    ],
    ids=["shared-engine-mismatch", "site-override-matches", "site-override-mismatches"],
)
def test_database_engine_uses_site_override_before_shared_settings(
    bench_path: Path, shared_engine: str, site_engine: str | None, expected_status: str
) -> None:
    sites = bench_path / "sites"
    (sites / "common_site_config.json").write_text(json.dumps({"db_type": shared_engine}))
    site_config = sites / "demo.localhost" / "site_config.json"
    values = json.loads(site_config.read_text())
    if site_engine is not None:
        values["db_type"] = site_engine
    site_config.write_text(json.dumps(values))

    report = BenchDiagnostics(bench_path).run()

    engine_checks = [check for check in report.checks if check.code == "site_database_engine"]
    assert [(check.path, check.status) for check in engine_checks] == [(str(site_config), expected_status)]
    assert report.has_errors is (expected_status == "error")


def test_missing_path_produces_json_errors_instead_of_a_traceback(tmp_path: Path) -> None:
    result = run_doctor(tmp_path / "missing", "--json")

    assert result.returncode == 1
    assert json.loads(result.stdout)["ok"] is False
    assert "Traceback" not in result.stderr


def test_default_path_finds_bench_from_a_site_subdirectory(bench_path: Path) -> None:
    result = run_doctor(None, "--json", cwd=bench_path / "sites" / "demo.localhost")

    assert result.returncode == 0
    assert json.loads(result.stdout)["path"] == str(bench_path)


def test_invalid_site_does_not_prevent_inspecting_the_next_site(bench_path: Path) -> None:
    invalid = bench_path / "sites" / "aaa.localhost"
    invalid.mkdir()
    (invalid / "site_config.json").write_text("[]")

    report = BenchDiagnostics(bench_path).run()

    assert report.has_errors
    site_checks = [check for check in report.checks if check.code == "site_config"]
    assert [check.status for check in site_checks] == ["error", "ok"]


def test_doctor_does_not_silently_ignore_global_bench_selection(bench_path: Path) -> None:
    result = run_doctor(bench_path, "-b", "other")

    assert result.returncode == 1
    assert "uses --path instead of -b" in result.stderr


@pytest.mark.parametrize("json_output", [True, False])
def test_slow_doctor_keeps_json_parseable_and_human_timing(bench_path, monkeypatch, capsys, json_output):
    from pilot.internal.cli import CliContext
    from pilot.internal.cli.dispatch import run_native

    ticks = iter([100.0, 103.0])
    monkeypatch.setattr("time.monotonic", lambda: next(ticks))
    arguments = ["doctor", "--path", str(bench_path)]
    if json_output:
        arguments.append("--json")
    run_native(CliContext(installation_root=REPO), arguments)

    output = capsys.readouterr().out
    if json_output:
        assert json.loads(output)["ok"] is True
    else:
        assert "Done in 3s" in output
