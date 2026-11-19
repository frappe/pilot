"""Tests for the site code editor: /api/v1/sites/<name>/editor/* and its sign-in on a site's /_pilot path."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import pytest

from admin.backend.app import create_app
from admin.backend.internal.session import Session
from admin.backend.middleware import AuthPolicy, get_auth_policy
from pilot.config import BenchConfig
from pilot.core.bench import Bench

SITE = "a.local"


@pytest.fixture
def bench_root(tmp_path: Path) -> Path:
    root = tmp_path / "bench"
    (root / "apps" / "widgets").mkdir(parents=True)
    for site in (SITE, "b.local"):
        (root / "sites" / site).mkdir(parents=True)
        (root / "sites" / site / "site_config.json").write_text(json.dumps({"db_password": "secret"}))
    site = root / "sites" / SITE
    (site / "themes" / "blue").mkdir(parents=True)
    (site / "themes" / "blue" / "style.css").write_text("body {}\n")
    (site / "notes.md").write_text("needle\n")
    for path in (
        "backups/site_config_backup.json",
        "private/backups/db.sql",
        "private/files/a.txt",
        "public/files/b.txt",
        "logs/web.log",
        "locks/x",
    ):
        (site / path).parent.mkdir(parents=True, exist_ok=True)
        (site / path).write_text("needle\n")
    (site / "private" / "notes.txt").write_text("needle\n")
    (root / "bench.toml").write_text(
        BenchConfig.from_flat(
            "bench", {"admin_enabled": True, "admin_password": "secret", "enable_code_editor": True}
        ).dumps()
    )
    return root


@pytest.fixture
def client(bench_root: Path):
    app = create_app(bench_root)
    app.config["TESTING"] = True
    return app.test_client()


def _bearer(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _editor_token(bench_root: Path, site: str = SITE) -> dict:
    return _bearer(Session(Bench(bench_root)).issue_session_token(scope="site_editor", site=site)[0])


MOUNTED = {"X-Forwarded-Prefix": "/_pilot"}


def test_sign_in_on_a_site_mount_opens_only_that_sites_editor(client, bench_root: Path) -> None:
    response = client.post(
        "/api/v1/auth/session", json={"password": "secret"}, headers=MOUNTED, base_url=f"https://{SITE}"
    )

    cookies = response.headers.getlist("Set-Cookie")
    token = cookies[0].split(";")[0].removeprefix("pilot_sid=")
    claims = Session(Bench(bench_root)).verify_token(token)
    assert response.get_json() == {"authenticated": True, "scope": "site_editor"}
    assert len(cookies) == 1 and "Path=/_pilot/" in cookies[0] and "HttpOnly" in cookies[0]
    assert (claims["scope"], claims["site"]) == ("site_editor", SITE)
    assert claims["exp"] - claims["iat"] == Session.SITE_EDITOR_TTL == 3 * 3600
    assert "Max-Age=10800" in cookies[0]


def test_sign_in_on_an_unknown_host_is_refused(client) -> None:
    response = client.post(
        "/api/v1/auth/session", json={"password": "secret"}, headers=MOUNTED, base_url="https://other.example"
    )

    assert response.status_code == 404
    assert "Set-Cookie" not in response.headers


def test_sign_out_on_a_site_mount_clears_only_pilots_cookie(client) -> None:
    response = client.delete("/api/v1/auth/session", headers=MOUNTED)

    cookie = response.headers["Set-Cookie"]
    assert cookie.startswith("pilot_sid=;") and "Path=/_pilot/" in cookie


def test_site_token_cannot_open_the_editor(client, bench_root: Path) -> None:
    token = Session(Bench(bench_root)).issue_site_token(SITE)

    response = client.get(f"/api/v1/sites/{SITE}/editor/tree", headers=_bearer(token))

    assert response.status_code == 403


def test_site_editor_opens_the_site_folder_without_its_excluded_paths(client, bench_root: Path) -> None:
    headers = _editor_token(bench_root)
    url = f"/api/v1/sites/{SITE}/editor"

    root = client.get(f"{url}/tree", headers=headers)
    private = client.get(f"{url}/tree", query_string={"path": "private"}, headers=headers)
    public = client.get(f"{url}/tree", query_string={"path": "public"}, headers=headers)
    files = client.get(f"{url}/files", headers=headers)
    search = client.get(f"{url}/search", query_string={"q": "needle"}, headers=headers)

    assert {entry["name"] for entry in root.get_json()} == {"themes", "notes.md", "private", "public"}
    assert [entry["name"] for entry in private.get_json()] == ["notes.txt"]
    assert public.get_json() == []
    assert sorted(files.get_json()) == ["notes.md", "private/notes.txt", "themes/blue/style.css"]
    assert sorted(match["file"] for match in search.get_json()) == ["notes.md", "private/notes.txt"]


@pytest.mark.parametrize(
    "path",
    [
        "site_config.json",
        "backups/site_config_backup.json",
        "private/backups/db.sql",
        "private/files/a.txt",
        "public/files/b.txt",
        "logs/web.log",
        "locks/x",
    ],
)
def test_site_editor_cannot_read_or_write_an_excluded_path(client, bench_root: Path, path: str) -> None:
    headers = _editor_token(bench_root)
    url = f"/api/v1/sites/{SITE}/editor"

    read = client.get(f"{url}/file", query_string={"path": path}, headers=headers)
    write = client.put(f"{url}/file", query_string={"path": path}, json={"content": "x"}, headers=headers)
    delete = client.delete(f"{url}/delete", query_string={"path": path}, headers=headers)

    assert [read.status_code, write.status_code, delete.status_code] == [403, 403, 403]
    assert read.get_json()["error"]["code"] == "excluded_path"
    assert (bench_root / "sites" / SITE / path).exists()


def test_site_editor_cannot_create_inside_an_excluded_folder(client, bench_root: Path) -> None:
    response = client.post(
        f"/api/v1/sites/{SITE}/editor/create",
        json={"path": "public/files/new.txt", "type": "file"},
        headers=_editor_token(bench_root),
    )

    assert response.status_code == 403
    assert not (bench_root / "sites" / SITE / "public" / "files" / "new.txt").exists()


def test_site_editor_cannot_move_or_delete_a_folder_holding_an_excluded_path(
    client, bench_root: Path
) -> None:
    headers = _editor_token(bench_root)
    url = f"/api/v1/sites/{SITE}/editor"

    delete = client.delete(f"{url}/delete", query_string={"path": "private"}, headers=headers)
    rename = client.post(f"{url}/rename", json={"from": "public", "to": "old-public"}, headers=headers)

    assert [delete.status_code, rename.status_code] == [403, 403]
    assert (bench_root / "sites" / SITE / "private" / "backups" / "db.sql").exists()
    assert (bench_root / "sites" / SITE / "public" / "files").is_dir()


def test_site_editor_cannot_reach_an_excluded_path_through_a_symlink(client, bench_root: Path) -> None:
    (bench_root / "sites" / SITE / "config-link.json").symlink_to("site_config.json")

    response = client.get(
        f"/api/v1/sites/{SITE}/editor/file",
        query_string={"path": "config-link.json"},
        headers=_editor_token(bench_root),
    )

    assert response.status_code == 403


def test_site_editor_cannot_reach_another_site(client, bench_root: Path) -> None:
    response = client.get(
        f"/api/v1/sites/{SITE}/editor/file",
        query_string={"path": "../b.local/site_config.json"},
        headers=_editor_token(bench_root),
    )

    assert response.status_code == 400


def test_base_opens_a_folder_inside_the_site_folder(client, bench_root: Path) -> None:
    headers = _editor_token(bench_root)
    url = f"/api/v1/sites/{SITE}/editor"

    base = client.get(f"{url}/tree", query_string={"base": "themes"}, headers=headers)
    above = client.get(f"{url}/tree", query_string={"base": ".."}, headers=headers)
    excluded = client.get(f"{url}/tree", query_string={"base": "logs"}, headers=headers)
    sibling = client.get(
        f"{url}/file", query_string={"base": "themes", "path": "../notes.md"}, headers=headers
    )

    assert [entry["name"] for entry in base.get_json()] == ["blue"]
    assert [above.status_code, excluded.status_code, sibling.status_code] == [400, 403, 400]


def test_site_editor_saves_into_the_site_folder(client, bench_root: Path) -> None:
    response = client.put(
        f"/api/v1/sites/{SITE}/editor/file",
        query_string={"path": "new/page.html"},
        json={"content": "<p>hi</p>"},
        headers=_editor_token(bench_root),
    )

    assert response.status_code == 200
    assert (bench_root / "sites" / SITE / "new" / "page.html").read_text() == "<p>hi</p>"


def test_site_editor_has_no_git_even_inside_a_repository(client, bench_root: Path) -> None:
    subprocess.run(["git", "init", "-q", str(bench_root)], check=True)
    headers = _editor_token(bench_root)
    url = f"/api/v1/sites/{SITE}/editor"

    status = client.get(f"{url}/git/status", headers=headers)
    commit = client.post(f"{url}/git/commit", json={"message": "x", "all": True}, headers=headers)

    assert status.get_json() == {"repo": False}
    assert commit.status_code == 409
    assert (
        subprocess.run(["git", "-C", str(bench_root), "rev-parse", "HEAD"], capture_output=True).returncode
        != 0
    )


def test_site_editor_keeps_its_own_state(client, bench_root: Path) -> None:
    headers = _editor_token(bench_root)
    client.put(f"/api/v1/sites/{SITE}/editor/state", json={"open": ["notes.md"]}, headers=headers)

    own = client.get(f"/api/v1/sites/{SITE}/editor/state", headers=headers)
    other = client.get("/api/v1/sites/b.local/editor/state", headers=_editor_token(bench_root, "b.local"))

    assert own.get_json() == {"open": ["notes.md"]}
    assert other.get_json() == {}


def test_bench_session_opens_a_site_editor(client, bench_root: Path) -> None:
    client.set_cookie("sid", Session(Bench(bench_root)).issue_session_token()[0])

    response = client.get(f"/api/v1/sites/{SITE}/editor/tree")

    assert response.status_code == 200


def test_editor_token_reaches_only_its_own_site_editor(client, bench_root: Path) -> None:
    app = client.application
    headers = _editor_token(bench_root)
    allowed, refused = [], []
    for rule in app.url_map.iter_rules():
        if (
            not rule.rule.startswith("/api/")
            or get_auth_policy(app.view_functions[rule.endpoint]) == AuthPolicy.OPEN
        ):
            continue
        url = re.sub(r"<(?:\w+:)?(\w+)>", lambda match: SITE if match.group(1) == "name" else "x1", rule.rule)
        for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
            status = client.open(url, method=method, json={}, headers=headers).status_code
            (allowed if status != 403 else refused).append((method, url))

    assert allowed
    assert all(url.startswith(f"/api/v1/sites/{SITE}/editor/") for _, url in allowed)
    assert ("GET", f"/api/v1/sites/{SITE}/configuration") in refused


def test_editor_and_sign_in_pages_take_the_site_mount_for_their_assets(bench_root: Path, monkeypatch) -> None:
    import admin.backend.app as admin_app

    dist = bench_root.parent / "static"
    for name in ("editor", "dashboard"):
        (dist / name).mkdir(parents=True)
        (dist / name / "index.html").write_text('<script src="./assets/index.js"></script>')
    monkeypatch.setattr(admin_app, "STATIC_DIR", dist)
    client = create_app(bench_root).test_client()

    editor = client.get(f"/editor/site/{SITE}", headers=MOUNTED)
    sign_in = client.get("/login", headers=MOUNTED)
    spoofed = client.get(f"/editor/site/{SITE}", headers={"X-Forwarded-Prefix": '/x"><script>'})
    admin = client.get("/login")

    assert editor.get_data(as_text=True) == '<script src="/_pilot/editor-assets/assets/index.js"></script>'
    assert sign_in.get_data(as_text=True) == '<script src="/_pilot/assets/index.js"></script>'
    assert spoofed.get_data(as_text=True) == '<script src="/editor-assets/assets/index.js"></script>'
    assert admin.get_data(as_text=True) == '<script src="/assets/index.js"></script>'


def test_a_custom_domain_signs_in_to_and_opens_its_sites_editor(client, bench_root: Path) -> None:
    (bench_root / "sites" / SITE / "site_config.json").write_text(
        json.dumps({"domains": ["shop.customer.com"]})
    )

    sign_in = client.post(
        "/api/v1/auth/session",
        json={"password": "secret"},
        headers=MOUNTED,
        base_url="https://shop.customer.com",
    )
    token = sign_in.headers["Set-Cookie"].split(";")[0].removeprefix("pilot_sid=")
    tree = client.get("/api/v1/sites/shop.customer.com/editor/tree", headers=_bearer(token))

    assert Session(Bench(bench_root)).verify_token(token)["site"] == SITE
    assert "themes" in {entry["name"] for entry in tree.get_json()}
