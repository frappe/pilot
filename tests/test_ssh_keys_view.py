"""Tests for /api/v1/ssh-keys success paths."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock, patch

from pilot.config.bench_toml_builder import BenchTomlBuilder


def _client(bench_root: Path, password: str = "secret"):
    from admin.backend.app import create_app
    from pilot.commands.admin.generate_session import ensure_jwt_secret, issue_token

    bench_root.mkdir(parents=True, exist_ok=True)
    (bench_root / "bench.toml").write_text(
        BenchTomlBuilder(bench_root.name, {"admin_enabled": True, "admin_password": password}).render()
    )
    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    client.set_cookie("sid", issue_token(secret))
    return client


def test_list_keys_returns_the_stored_keys(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    key = Mock(fingerprint="SHA256:abc", key_type="ssh-ed25519", comment="me@laptop")

    with patch("admin.backend.api.v1.ssh_keys.AuthorizedKeysStore") as store:
        store.return_value.list.return_value = [key]
        response = client.get("/api/v1/ssh-keys")

    assert response.status_code == 200
    assert response.get_json() == {
        "keys": [{"fingerprint": "SHA256:abc", "type": "ssh-ed25519", "comment": "me@laptop"}]
    }


def test_add_key_returns_201_with_the_created_resource(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)
    key = Mock(fingerprint="SHA256:abc", key_type="ssh-ed25519", comment="me@laptop")

    with patch("admin.backend.api.v1.ssh_keys.AuthorizedKeysStore") as store:
        store.return_value.add.return_value = key
        response = client.post(
            "/api/v1/ssh-keys", json={"public_key": "ssh-ed25519 AAAA me@laptop"}
        )

    body = response.get_json()
    assert response.status_code == 201
    assert body == {"fingerprint": "SHA256:abc", "type": "ssh-ed25519", "comment": "me@laptop"}
    assert response.headers["Location"] == "/api/v1/ssh-keys/SHA256:abc"
    assert "ok" not in body


def test_remove_key_returns_204(tmp_path: Path) -> None:
    bench_root = tmp_path / "benches" / "current"
    client = _client(bench_root)

    with patch("admin.backend.api.v1.ssh_keys.AuthorizedKeysStore") as store:
        response = client.delete("/api/v1/ssh-keys/SHA256:abc")
        store.return_value.remove.assert_called_once_with("SHA256:abc")

    assert response.status_code == 204
    assert response.data == b""
