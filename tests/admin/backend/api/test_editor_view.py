"""Tests for the integrated code editor API (/api/v1/editor/*)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pilot.config import BenchConfig


def _bench(tmp_path: Path, developer_mode: bool = True) -> Path:
    bench_root = tmp_path / "benches" / "current"
    (bench_root / "apps" / "widgets").mkdir(parents=True)
    (bench_root / "apps" / "widgets" / "hello.py").write_text("print('hi')\n")
    (bench_root / "sites").mkdir(parents=True)
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat(
            bench_root.name,
            {
                "admin_enabled": True,
                "admin_password": "secret",
                "allow_developer_mode": developer_mode,
            },
        ).dumps()
    )
    return bench_root


def _client(bench_root: Path, scope: str = "bench"):
    from admin.backend.app import create_app
    from admin.backend.auth import ensure_jwt_secret, issue_site_token, issue_token

    secret = ensure_jwt_secret(bench_root / "bench.toml")
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    token = issue_token(secret) if scope == "bench" else issue_site_token(secret, "site.local")
    client.set_cookie("sid", token)
    return client


def test_tree_lists_app_files(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path))
    response = client.get("/api/v1/editor/tree", query_string={"app": "widgets"})
    names = {e["name"] for e in response.get_json()}
    assert response.status_code == 200
    assert "hello.py" in names


def test_read_returns_content_and_etag(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path))
    response = client.get(
        "/api/v1/editor/file", query_string={"app": "widgets", "path": "hello.py"}
    )
    body = response.get_json()
    assert body["content"] == "print('hi')\n"
    assert body["etag"] and response.headers["ETag"] == body["etag"]


def test_save_conflicts_on_stale_etag(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path))
    response = client.put(
        "/api/v1/editor/file",
        query_string={"app": "widgets", "path": "hello.py"},
        json={"content": "changed"},
        headers={"If-Match": "deadbeef"},
    )
    assert response.status_code == 409
    assert response.get_json()["content"] == "print('hi')\n"


def test_save_writes_with_wildcard_etag(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    client = _client(bench_root)
    response = client.put(
        "/api/v1/editor/file",
        query_string={"app": "widgets", "path": "sub/new.txt"},
        json={"content": "data"},
        headers={"If-Match": "*"},
    )
    assert response.status_code == 200
    assert (bench_root / "apps" / "widgets" / "sub" / "new.txt").read_text() == "data"


def test_path_traversal_is_neutralized(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    (bench_root / "secret.txt").write_text("top secret")
    client = _client(bench_root)
    response = client.get(
        "/api/v1/editor/file", query_string={"app": "widgets", "path": "../../secret.txt"}
    )
    # ".." is collapsed to the root, so it maps to a nonexistent in-app file.
    assert response.status_code == 404


def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    (bench_root / "secret.txt").write_text("top secret")
    (bench_root / "apps" / "widgets" / "escape").symlink_to(bench_root / "secret.txt")
    client = _client(bench_root)
    response = client.get(
        "/api/v1/editor/file", query_string={"app": "widgets", "path": "escape"}
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_path"


def test_delete_refuses_root(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path))
    response = client.delete(
        "/api/v1/editor/delete", query_string={"app": "widgets", "path": ""}
    )
    assert response.status_code == 400


def test_unknown_app_is_404(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path))
    response = client.get("/api/v1/editor/tree", query_string={"app": "ghost"})
    assert response.status_code == 404


def test_site_scoped_token_is_forbidden(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path), scope="site")
    response = client.get("/api/v1/editor/tree", query_string={"app": "widgets"})
    assert response.status_code == 403


def test_editor_is_forbidden_when_developer_mode_off(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path, developer_mode=False))
    response = client.get("/api/v1/editor/tree", query_string={"app": "widgets"})
    assert response.status_code == 403
    assert response.get_json()["error"]["code"] == "editor_disabled"


def test_state_roundtrips(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path))
    empty = client.get("/api/v1/editor/state", query_string={"app": "widgets"})
    assert empty.get_json() == {}
    client.put(
        "/api/v1/editor/state",
        query_string={"app": "widgets"},
        json={"tabs": ["hello.py"]},
    )
    saved = client.get("/api/v1/editor/state", query_string={"app": "widgets"})
    assert saved.get_json() == {"tabs": ["hello.py"]}


def test_git_status_and_stage_roundtrip(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    app_dir = bench_root / "apps" / "widgets"
    for args in (
        ["init", "-q"],
        ["config", "user.email", "t@t"],
        ["config", "user.name", "t"],
        ["add", "-A"],
        ["commit", "-qm", "init"],
    ):
        subprocess.run(["git", *args], cwd=app_dir, check=True)
    (app_dir / "hello.py").write_text("print('changed')\n")

    client = _client(bench_root)
    status = client.get("/api/v1/editor/git/status", query_string={"app": "widgets"}).get_json()
    assert status["repo"] is True
    assert any(f["path"] == "hello.py" for f in status["unstaged"])

    staged = client.post(
        "/api/v1/editor/git/stage",
        query_string={"app": "widgets"},
        json={"path": "hello.py"},
    )
    assert staged.status_code == 200 and staged.get_json()["ok"] is True
    after = client.get("/api/v1/editor/git/status", query_string={"app": "widgets"}).get_json()
    assert any(f["path"] == "hello.py" for f in after["staged"])
