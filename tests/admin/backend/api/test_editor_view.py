"""Tests for the integrated code editor API (/api/v1/editor/*)."""

from __future__ import annotations

import subprocess
from pathlib import Path

from pilot.config import BenchConfig


def _bench(tmp_path: Path, code_editor: bool = True) -> Path:
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
                "enable_code_editor": code_editor,
            },
        ).dumps()
    )
    return bench_root


def _client(bench_root: Path, scope: str = "bench"):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    session = Session(Bench(bench_root))
    app = create_app(bench_root)
    app.config["TESTING"] = True
    client = app.test_client()
    token = session.issue_session_token()[0] if scope == "bench" else session.issue_site_token("site.local")
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


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    (bench_root / "secret.txt").write_text("top secret")
    client = _client(bench_root)
    response = client.get(
        "/api/v1/editor/file", query_string={"app": "widgets", "path": "../../secret.txt"}
    )
    assert response.status_code == 400
    assert response.get_json()["error"]["code"] == "invalid_path"


def test_absolute_path_is_rejected(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    (bench_root / "secret.txt").write_text("top secret")
    client = _client(bench_root)
    for path in ("/etc/passwd", str(bench_root / "secret.txt")):
        response = client.get(
            "/api/v1/editor/file", query_string={"app": "widgets", "path": path}
        )
        assert response.status_code == 400, path
        assert response.get_json()["error"]["code"] == "invalid_path"


def test_write_routes_reject_escaping_paths(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    client = _client(bench_root)
    calls = [
        client.put(
            "/api/v1/editor/file",
            query_string={"app": "widgets", "path": "/tmp/pwned.txt"},
            json={"content": "x"},
        ),
        client.post(
            "/api/v1/editor/create",
            query_string={"app": "widgets"},
            json={"path": "../pwned.txt", "type": "file"},
        ),
        client.post(
            "/api/v1/editor/rename",
            query_string={"app": "widgets"},
            json={"from": "hello.py", "to": "../pwned.py"},
        ),
        client.delete(
            "/api/v1/editor/delete", query_string={"app": "widgets", "path": "../../secret.txt"}
        ),
        client.post(
            "/api/v1/editor/replace",
            query_string={"app": "widgets"},
            json={"query": "hi", "replace": "bye", "files": ["../../secret.txt"]},
        ),
    ]
    assert [r.status_code for r in calls] == [400] * len(calls)
    assert not (tmp_path / "pwned.txt").exists()
    assert (bench_root / "apps" / "widgets" / "hello.py").exists()


def test_git_routes_reject_escaping_paths(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path))
    for route in ("git/blame", "git/diff", "git/show"):
        response = client.get(
            f"/api/v1/editor/{route}", query_string={"app": "widgets", "path": "../../secret.txt"}
        )
        assert response.status_code == 400, route
    for route in ("git/stage", "git/unstage", "git/discard"):
        response = client.post(
            f"/api/v1/editor/{route}",
            query_string={"app": "widgets"},
            json={"path": "/etc/passwd"},
        )
        assert response.status_code == 400, route


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


def test_search_reports_a_broken_ripgrep(tmp_path: Path, monkeypatch) -> None:
    from admin.backend.core.editor import search as search_service

    def missing(*args, **kwargs):
        raise FileNotFoundError("rg")

    monkeypatch.setattr(search_service.subprocess, "run", missing)
    client = _client(_bench(tmp_path))
    response = client.get(
        "/api/v1/editor/search", query_string={"app": "widgets", "q": "hi"}
    )
    assert response.status_code == 503
    assert response.get_json()["error"]["code"] == "search_unavailable"


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


def test_editor_works_without_developer_mode(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path))
    response = client.get("/api/v1/editor/tree", query_string={"app": "widgets"})
    assert response.status_code == 200


def test_editor_is_forbidden_when_turned_off(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path, code_editor=False))
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


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def _commit_all(cwd: Path) -> None:
    _git(cwd, "init", "-q")
    _git(cwd, "config", "user.email", "t@t")
    _git(cwd, "config", "user.name", "t")
    _git(cwd, "add", "-A")
    _git(cwd, "commit", "-qm", "init")


def test_symlinked_folder_escape_is_rejected(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("top secret")
    (bench_root / "apps" / "widgets" / "linked").symlink_to(outside)
    client = _client(bench_root)
    app = {"app": "widgets"}
    calls = [
        client.get("/api/v1/editor/tree", query_string={**app, "path": "linked"}),
        client.get("/api/v1/editor/file", query_string={**app, "path": "linked/secret.txt"}),
        client.put(
            "/api/v1/editor/file", query_string={**app, "path": "linked/new.txt"}, json={"content": "x"}
        ),
        client.post("/api/v1/editor/create", query_string=app, json={"path": "linked/made", "type": "dir"}),
        client.post(
            "/api/v1/editor/rename", query_string=app, json={"from": "hello.py", "to": "linked/moved.py"}
        ),
        client.post(
            "/api/v1/editor/rename", query_string=app, json={"from": "linked/secret.txt", "to": "stolen.txt"}
        ),
        client.delete("/api/v1/editor/delete", query_string={**app, "path": "linked/secret.txt"}),
        client.post(
            "/api/v1/editor/replace", query_string=app, json={"query": "top", "files": ["linked/secret.txt"]}
        ),
    ]
    assert [r.status_code for r in calls] == [400] * len(calls)
    assert sorted(p.name for p in outside.iterdir()) == ["secret.txt"]
    assert (outside / "secret.txt").read_text() == "top secret"


def test_dangling_symlink_cannot_create_a_file_outside(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    (bench_root / "apps" / "widgets" / "dangling").symlink_to(tmp_path / "created.txt")
    client = _client(bench_root)
    response = client.put(
        "/api/v1/editor/file", query_string={"app": "widgets", "path": "dangling"}, json={"content": "x"}
    )
    assert response.status_code == 400
    assert not (tmp_path / "created.txt").exists()


def test_malformed_paths_are_rejected(tmp_path: Path) -> None:
    client = _client(_bench(tmp_path))
    app = {"app": "widgets"}
    calls = [
        client.get("/api/v1/editor/file", query_string={**app, "path": "hello.py\x00.txt"}),
        client.post("/api/v1/editor/create", query_string=app, json={"path": 5}),
        client.post("/api/v1/editor/rename", query_string=app, json={"from": "hello.py", "to": ["x"]}),
        client.post("/api/v1/editor/git/stage", query_string=app, json={"path": {"x": 1}}),
        client.post("/api/v1/editor/replace", query_string=app, json={"query": "hi", "files": [1]}),
    ]
    assert [r.status_code for r in calls] == [400] * len(calls)


def test_checkout_refuses_option_like_branch_names(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    _commit_all(bench_root / "apps" / "widgets")
    secret = tmp_path / "secret.txt"
    secret.write_text("secret-outside-line\n")
    client = _client(bench_root)
    for branch in (f"--pathspec-from-file={secret}", "-f", "bad..name", 5):
        response = client.post(
            "/api/v1/editor/git/checkout", query_string={"app": "widgets"}, json={"branch": branch}
        )
        assert response.status_code == 400, branch
        assert "secret-outside-line" not in response.get_data(as_text=True)

    created = client.post(
        "/api/v1/editor/git/checkout",
        query_string={"app": "widgets"},
        json={"branch": "feature", "create": True},
    )
    assert created.status_code == 200


def test_git_pathspec_magic_cannot_reach_outside_the_app(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    apps = bench_root / "apps"
    (apps / "top.txt").write_text("original\n")
    _commit_all(apps)
    (apps / "top.txt").write_text("changed\n")
    client = _client(bench_root)
    for route in ("git/stage", "git/discard"):
        response = client.post(
            f"/api/v1/editor/{route}", query_string={"app": "widgets"}, json={"path": ":/top.txt"}
        )
        assert response.status_code != 200 or response.get_json()["ok"] is False, route
    assert (apps / "top.txt").read_text() == "changed\n"
    assert _git(apps, "diff", "--cached", "--name-only") == ""


def test_dot_files_cannot_be_opened_or_changed(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    app_dir = bench_root / "apps" / "widgets"
    (app_dir / ".env").write_text("SECRET=1\n")
    (app_dir / ".github").mkdir()
    (app_dir / ".github" / "ci.yml").write_text("on: push\n")
    _commit_all(app_dir)
    client = _client(bench_root)
    app = {"app": "widgets"}
    for path in (".env", ".git/config", ".github/ci.yml", "sub/.secret"):
        calls = [
            client.get("/api/v1/editor/file", query_string={**app, "path": path}),
            client.get("/api/v1/editor/tree", query_string={**app, "path": path}),
            client.get("/api/v1/editor/git/blame", query_string={**app, "path": path}),
            client.put("/api/v1/editor/file", query_string={**app, "path": path}, json={"content": "x"}),
            client.post("/api/v1/editor/create", query_string=app, json={"path": path}),
            client.post("/api/v1/editor/rename", query_string=app, json={"from": "hello.py", "to": path}),
            client.post("/api/v1/editor/rename", query_string=app, json={"from": path, "to": "out.txt"}),
            client.delete("/api/v1/editor/delete", query_string={**app, "path": path}),
            client.post("/api/v1/editor/replace", query_string=app, json={"query": "x", "files": [path]}),
            client.post("/api/v1/editor/git/discard", query_string=app, json={"path": path}),
        ]
        assert [r.status_code for r in calls] == [403] * len(calls), path
        assert {r.get_json()["error"]["code"] for r in calls} == {"hidden_path"}
    assert (app_dir / ".env").read_text() == "SECRET=1\n"
    assert not (app_dir / "out.txt").exists()


def test_symlink_to_a_dot_file_is_refused(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    app_dir = bench_root / "apps" / "widgets"
    (app_dir / ".env").write_text("SECRET=1\n")
    (app_dir / "settings").symlink_to(app_dir / ".env")
    response = _client(bench_root).get(
        "/api/v1/editor/file", query_string={"app": "widgets", "path": "settings"}
    )
    assert response.status_code == 403


def test_folders_holding_dot_files_cannot_be_deleted_or_moved(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    nested = bench_root / "apps" / "widgets" / "vendor"
    (nested / ".git").mkdir(parents=True)
    client = _client(bench_root)
    app = {"app": "widgets"}
    calls = [
        client.delete("/api/v1/editor/delete", query_string={**app, "path": "vendor"}),
        client.post("/api/v1/editor/rename", query_string=app, json={"from": "vendor", "to": "moved"}),
    ]
    assert [r.status_code for r in calls] == [403, 403]
    assert (nested / ".git").is_dir()


def test_dot_files_stay_listed_but_out_of_quick_open_and_search(tmp_path: Path) -> None:
    bench_root = _bench(tmp_path)
    app_dir = bench_root / "apps" / "widgets"
    (app_dir / ".env").write_text("print('hi')\n")
    client = _client(bench_root)
    app = {"app": "widgets"}

    tree = client.get("/api/v1/editor/tree", query_string=app).get_json()
    assert ".env" in [entry["name"] for entry in tree]
    assert client.get("/api/v1/editor/files", query_string=app).get_json() == ["hello.py"]

    search = client.get("/api/v1/editor/search", query_string={**app, "q": "hi"})
    if search.status_code == 200:
        assert [result["file"] for result in search.get_json()] == ["hello.py"]
