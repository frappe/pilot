from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from pilot.config.bench_toml_builder import BenchTomlBuilder
from pilot.core.diagnostics import DiagnosticCheck


def _client(bench_root: Path):
    from admin.backend.app import create_app
    from pilot.commands.generate_session import ensure_jwt_secret, issue_token

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchTomlBuilder("diag", {"admin_enabled": True, "admin_password": "secret"}).render()
    )
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return client


def test_api_diagnostics_returns_report(tmp_path: Path) -> None:
    client = _client(tmp_path / "benches" / "diag")
    checks = [DiagnosticCheck("bench", "bench.toml", "ok", "present")]

    with patch("admin.backend.views.diagnostics.DiagnosticRunner.run", return_value=checks):
        response = client.get("/api/diagnostics/")

    payload = response.get_json()
    assert payload["bench"] == "diag"
    assert payload["checks"][0]["name"] == "bench.toml"
