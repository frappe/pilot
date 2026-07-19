from __future__ import annotations

from pathlib import Path
from admin.backend.app import create_app


def test_setup_production_invalid_bench_name(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    client = app.test_client()

    res = client.post("/api/v1/benches/invalid;bench/actions/setup-production", json={"admin_domain": "pilot.example.com"})
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "invalid_bench_name"


def test_setup_production_invalid_domain(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    client = app.test_client()

    # Create dummy bench directory with bench.toml
    bench_dir = tmp_path.parent / "testbench"
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text("[admin]\nport = 8000\n[production]\nenabled = false\n", encoding="utf-8")

    res = client.post("/api/v1/benches/testbench/actions/setup-production", json={"admin_domain": "invalid_domain;script"})
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "invalid_admin_domain"


def test_setup_production_invalid_process_manager(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    client = app.test_client()

    bench_dir = tmp_path.parent / "testbench"
    bench_dir.mkdir(parents=True, exist_ok=True)
    (bench_dir / "bench.toml").write_text("[admin]\nport = 8000\n[production]\nenabled = false\n", encoding="utf-8")

    res = client.post("/api/v1/benches/testbench/actions/setup-production", json={"admin_domain": "pilot.example.com", "process_manager": "invalid_pm"})
    assert res.status_code == 422
    assert res.get_json()["error"]["code"] == "invalid_process_manager"


def test_setup_production_domain_conflict(tmp_path: Path) -> None:
    app = create_app(tmp_path)
    client = app.test_client()

    # Bench 1 with admin domain
    bench1_dir = tmp_path.parent / "bench1"
    bench1_dir.mkdir(parents=True, exist_ok=True)
    (bench1_dir / "bench.toml").write_text("[admin]\nport = 8000\ndomain = \"admin.example.com\"\n[production]\nenabled = true\n", encoding="utf-8")

    # Bench 2 trying to setup production with the same admin domain
    bench2_dir = tmp_path.parent / "bench2"
    bench2_dir.mkdir(parents=True, exist_ok=True)
    (bench2_dir / "bench.toml").write_text("[admin]\nport = 8002\n[production]\nenabled = false\n", encoding="utf-8")

    res = client.post("/api/v1/benches/bench2/actions/setup-production", json={"admin_domain": "admin.example.com"})
    assert res.status_code == 409
    assert res.get_json()["error"]["code"] == "admin_domain_conflict"

