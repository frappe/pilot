"""Property tests: no client path or branch name lets the editor leave the app or touch dot files."""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from admin.backend.core.editor.workspace import (
    EditorPathError,
    EditorWorkspace,
    HiddenPathError,
    _clean_relative,
)
from pilot.config import BenchConfig

SECRET = "secret-outside-line"
FUZZ = settings(max_examples=300, deadline=None, suppress_health_check=[HealthCheck.too_slow])
GIT_FUZZ = settings(max_examples=60, deadline=None, suppress_health_check=[HealthCheck.too_slow])

SEGMENTS = st.sampled_from(
    [
        "..",
        ".",
        "",
        " ",
        "~",
        "hello.py",
        "sub",
        "inner.py",
        ".env",
        ".git",
        ".github",
        "config",
        "link_out_dir",
        "link_out_file",
        "link_dot",
        "link_in",
        "dangling",
        "loop",
        "top.txt",
        ":",
        ":/top.txt",
        ":(top)top.txt",
        ":!hello.py",
        "*",
        "%2e%2e",
        "..\\..",
        "\\",
        "\x00",
        "-",
        "--",
        "secret.txt",
    ]
)
KNOWN_ATTACKS = [
    "../../outside/secret.txt",
    "/etc/passwd",
    ":/top.txt",
    ":(top)top.txt",
    "link_out_file",
    "link_out_dir/secret.txt",
    "link_dot",
    "dangling",
    "loop",
    ".env",
    ".git/config",
    "/",
    ".",
    "sub/../../top.txt",
    "hello.py\x00",
]
PATHS = st.one_of(
    st.builds(
        lambda prefix, parts: prefix + "/".join(parts),
        st.sampled_from(["", "/", "./", " ", "//", "../"]),
        st.lists(st.one_of(SEGMENTS, st.text(max_size=6)), min_size=1, max_size=5),
    ),
    st.text(max_size=20),
)


@pytest.fixture(scope="module")
def sandbox(tmp_path_factory) -> Path:
    """A bench whose app sits inside a larger repo, beside an outside secret and hostile symlinks."""
    root = tmp_path_factory.mktemp("sandbox")
    outside = root / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text(f"{SECRET}\n")

    bench_root = root / "bench"
    apps = bench_root / "apps"
    app = apps / "widgets"
    (app / "sub").mkdir(parents=True)
    (app / ".github").mkdir()
    (bench_root / "sites").mkdir()
    (bench_root / "bench.toml").write_text(
        BenchConfig.from_flat("bench", {"admin_enabled": True, "admin_password": "secret"}).dumps()
    )
    (apps / "top.txt").write_text("top\n")
    (app / "hello.py").write_text("print('hi')\n")
    (app / "sub" / "inner.py").write_text("print('inner')\n")
    (app / ".env").write_text("SECRET=1\n")
    (app / ".github" / "ci.yml").write_text("on: push\n")

    (app / "link_out_dir").symlink_to(outside)
    (app / "link_out_file").symlink_to(outside / "secret.txt")
    (app / "link_dot").symlink_to(app / ".env")
    (app / "link_in").symlink_to(app / "sub")
    (app / "dangling").symlink_to(root / "created.txt")
    (app / "loop").symlink_to(app / "loop")

    for args in (["init", "-q"], ["config", "user.email", "t@t"], ["config", "user.name", "t"]):
        subprocess.run(["git", *args], cwd=apps, check=True)
    subprocess.run(["git", "add", "top.txt", "widgets/hello.py", "widgets/.env"], cwd=apps, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=apps, check=True)
    # Uncommitted changes outside the app and in a dot file, so a stray stage or discard shows.
    (apps / "top.txt").write_text("top, changed\n")
    (app / ".env").write_text("SECRET=2\n")
    return root


@pytest.fixture(scope="module")
def client(sandbox: Path):
    from admin.backend.app import create_app
    from admin.backend.internal.session import Session
    from pilot.core.bench import Bench

    bench_root = sandbox / "bench"
    app = create_app(bench_root)
    app.config["TESTING"] = True
    test_client = app.test_client()
    test_client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])
    return test_client


def _protected_state(sandbox: Path) -> dict[str, object]:
    """Files and folder listings outside the app, plus the app's dot files; none may ever change."""
    apps = sandbox / "bench" / "apps"
    app = apps / "widgets"
    files = [sandbox / "outside" / "secret.txt", apps / "top.txt", app / ".env", app / ".github" / "ci.yml"]
    state: dict[str, object] = {
        str(path.relative_to(sandbox)): hashlib.sha256(path.read_bytes()).hexdigest() for path in files
    }
    for directory in (sandbox, sandbox / "outside", apps, app / ".github"):
        state[f"{directory}/"] = sorted(os.listdir(directory))
    state["app dot entries"] = sorted(name for name in os.listdir(app) if name.startswith("."))
    return state


@FUZZ
@given(st.text(max_size=40))
def test_cleaned_paths_never_leave_the_app(text: str) -> None:
    try:
        clean = _clean_relative(text)
    except EditorPathError:
        return
    assert not clean.startswith("/")
    assert "\x00" not in clean
    assert ".." not in clean.split("/")


@FUZZ
@given(st.one_of(PATHS, st.sampled_from(KNOWN_ATTACKS)))
def test_safe_paths_resolve_inside_the_app_and_never_to_dot_files(sandbox: Path, path: str) -> None:
    workspace = EditorWorkspace(sandbox / "bench" / "apps" / "widgets")
    try:
        target = workspace.safe(path)
    except (EditorPathError, HiddenPathError):
        return
    resolved = target.resolve()
    assert resolved == workspace.root_real or workspace.root_real in resolved.parents
    inside = resolved.relative_to(workspace.root_real).parts
    assert not any(part.startswith(".") for part in inside), path


@FUZZ
@given(PATHS, PATHS)
@example("link_out_dir/secret.txt", "../../outside/stolen.txt")
@example("dangling", "link_out_dir/new.txt")
@example(".env", "link_out_file")
@example("loop", "")
@example("", "")
@example("sub", "sub/moved")
def test_file_routes_never_fail_or_touch_protected_files(
    sandbox: Path, client, path: str, other: str
) -> None:
    before = _protected_state(sandbox)
    app = {"app": "widgets"}
    responses = [
        client.get("/api/v1/editor/file", query_string={**app, "path": path}),
        client.get("/api/v1/editor/tree", query_string={**app, "path": path}),
        client.put("/api/v1/editor/file", query_string={**app, "path": path}, json={"content": "fuzz"}),
        client.post("/api/v1/editor/create", query_string=app, json={"path": path, "type": "file"}),
        client.post("/api/v1/editor/rename", query_string=app, json={"from": path, "to": other}),
        client.post(
            "/api/v1/editor/replace", query_string=app, json={"query": "fuzz", "files": [path, other]}
        ),
        client.delete("/api/v1/editor/delete", query_string={**app, "path": other}),
    ]
    assert all(response.status_code < 500 for response in responses), [r.status_code for r in responses]
    assert all(SECRET not in response.get_data(as_text=True) for response in responses)
    assert _protected_state(sandbox) == before
    assert not (sandbox / "created.txt").exists()


@GIT_FUZZ
@given(PATHS)
@example(":/top.txt")
@example(":(top)top.txt")
@example("/")
@example(".env")
def test_git_routes_never_reach_outside_the_app(sandbox: Path, client, path: str) -> None:
    before = _protected_state(sandbox)
    app = {"app": "widgets"}
    responses = [
        client.get("/api/v1/editor/git/blame", query_string={**app, "path": path}),
        client.get("/api/v1/editor/git/show", query_string={**app, "path": path}),
        client.post("/api/v1/editor/git/stage", query_string=app, json={"path": path}),
        client.post("/api/v1/editor/git/discard", query_string=app, json={"path": path}),
        client.post("/api/v1/editor/git/commit", query_string=app, json={"message": "fuzz", "all": False}),
        client.post("/api/v1/editor/git/commit", query_string=app, json={"message": "fuzz", "all": True}),
    ]
    assert all(response.status_code < 500 for response in responses), [r.status_code for r in responses]
    assert all(SECRET not in response.get_data(as_text=True) for response in responses)
    assert _protected_state(sandbox) == before
    staged = _git_names(sandbox, "diff", "-z", "--cached", "--name-only")
    root = _git_names(sandbox, "rev-list", "-z", "--max-parents=0", "HEAD")[0]
    committed = _git_names(sandbox, "diff", "-z", "--name-only", root, "HEAD")
    for name in [*staged, *committed]:
        assert name.startswith("widgets/") and "/." not in name, name


def _git_names(sandbox: Path, *args: str) -> list[str]:
    result = subprocess.run(["git", *args], cwd=sandbox / "bench" / "apps", capture_output=True, text=True)
    return [name for name in result.stdout.split("\x00") if name.strip()]


@GIT_FUZZ
@given(
    st.one_of(
        st.text(max_size=30),
        st.builds(
            lambda option, value: option + value,
            st.sampled_from(["-", "--", "--pathspec-from-file=", "-b", "--orphan=", "--detach "]),
            st.sampled_from(["", "x", "../outside/secret.txt"]),
        ),
    )
)
@example("\x00")
@example("--pathspec-from-file=../outside/secret.txt")
def test_checkout_never_takes_an_option_or_leaks_a_file(sandbox: Path, client, branch: str) -> None:
    secret = str(sandbox / "outside" / "secret.txt")
    response = client.post(
        "/api/v1/editor/git/checkout",
        query_string={"app": "widgets"},
        json={"branch": branch.replace("../outside/secret.txt", secret)},
    )
    assert response.status_code < 500
    assert SECRET not in response.get_data(as_text=True)
    if branch.startswith("-"):
        assert response.status_code == 400, branch
