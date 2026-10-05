from __future__ import annotations

import functools
import hashlib
import io
import json
import subprocess
import tarfile
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

from pilot.core.app.prebuilt_assets import PrebuiltAssets
from pilot.exceptions import BenchError


def _app(tmp_path: Path) -> SimpleNamespace:
    path = tmp_path / "apps" / "myapp"
    (path / "myapp" / "public" / "dist" / "js").mkdir(parents=True)
    (path / "myapp" / "public" / "dist" / "js" / "old.bundle.AAAAAAAA.js").write_text("old")
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    subprocess.run(["git", "-C", str(path), "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-q", "--allow-empty", "-m", "c"], check=True)
    commit = subprocess.run(["git", "-C", str(path), "rev-parse", "HEAD"], capture_output=True, text=True).stdout.strip()
    return SimpleNamespace(path=path, installed_hash=commit, config=SimpleNamespace(name="myapp"))


def _publish(directory: Path, app, manifest: dict | None = None, checksum: str | None = None) -> None:
    """What scripts/build-app-assets.sh writes: the archive and its .sha256."""
    name = f"myapp-{app.installed_hash}.tar.gz"
    manifest = manifest or {"app": "myapp", "commit": app.installed_hash, "paths": ["myapp/public/dist", "myapp/www/x.html"]}
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        files = {
            "manifest.json": json.dumps(manifest),
            "myapp/public/dist/js/new.bundle.BBBBBBBB.js": "new",
            "myapp/www/x.html": "<html>",
        }
        for member, content in files.items():
            data = content.encode()
            info = tarfile.TarInfo(member)
            info.size = len(data)
            archive.addfile(info, io.BytesIO(data))
    (directory / name).write_bytes(buffer.getvalue())
    (directory / f"{name}.sha256").write_text(f"{checksum or hashlib.sha256(buffer.getvalue()).hexdigest()}  {name}\n")


@pytest.fixture
def release(tmp_path: Path, monkeypatch):
    directory = tmp_path / "release"
    directory.mkdir()
    handler = functools.partial(SimpleHTTPRequestHandler, directory=str(directory))
    handler.log_message = lambda *args: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    monkeypatch.setattr(PrebuiltAssets, "release_url", f"http://127.0.0.1:{server.server_port}")
    yield directory
    server.shutdown()


def test_published_assets_replace_the_old_build(tmp_path: Path, release: Path) -> None:
    app = _app(tmp_path)
    _publish(release, app)

    assert PrebuiltAssets(app).install() is True

    dist = app.path / "myapp" / "public" / "dist" / "js"
    assert [path.name for path in dist.iterdir()] == ["new.bundle.BBBBBBBB.js"]
    assert (app.path / "myapp" / "www" / "x.html").read_text() == "<html>"


def test_no_assets_for_this_commit_means_build(tmp_path: Path, release: Path) -> None:
    assert PrebuiltAssets(_app(tmp_path)).install() is False


def test_a_checksum_mismatch_is_an_error_not_a_silent_build(tmp_path: Path, release: Path) -> None:
    app = _app(tmp_path)
    _publish(release, app, checksum="0" * 64)

    with pytest.raises(BenchError, match="checksum"):
        PrebuiltAssets(app).install()


@pytest.mark.parametrize(
    ("manifest", "message"),
    [
        ({"app": "myapp", "commit": "0" * 40, "paths": []}, "another commit"),
        ({"app": "myapp", "commit": None, "paths": ["../../escape"]}, "outside the app"),
        ({"app": "myapp", "commit": None, "paths": ["."]}, "outside the app"),
        ({"app": "myapp", "commit": None, "paths": ["myapp/public/dist", "myapp/public/missing"]}, "do not contain"),
    ],
)
def test_a_wrong_manifest_is_refused(tmp_path: Path, release: Path, manifest: dict, message: str) -> None:
    app = _app(tmp_path)
    if manifest["commit"] is None:
        manifest["commit"] = app.installed_hash
    _publish(release, app, manifest=manifest)

    with pytest.raises(BenchError, match=message):
        PrebuiltAssets(app).install()


def test_a_refused_archive_leaves_the_current_build_in_place(tmp_path: Path, release: Path) -> None:
    app = _app(tmp_path)
    paths = ["myapp/public/dist", "myapp/public/missing"]
    _publish(release, app, manifest={"app": "myapp", "commit": app.installed_hash, "paths": paths})

    with pytest.raises(BenchError):
        PrebuiltAssets(app).install()

    assert (app.path / "myapp" / "public" / "dist" / "js" / "old.bundle.AAAAAAAA.js").exists()


def _manifest(app, **extra) -> dict:
    return {"app": "myapp", "commit": app.installed_hash, "paths": ["myapp/public/dist", "myapp/www/x.html"], **extra}


def test_the_build_s_assets_json_entries_travel_with_the_archive(tmp_path: Path, release: Path) -> None:
    # Keys such as Frappe page islands cannot be guessed from file names.
    app = _app(tmp_path)
    entries = {"myapp.dashboard.island.js": "/assets/myapp/dist/island/dashboard.ABCD1234.js"}
    _publish(release, app, _manifest(app, **{"assets.json": entries, "assets-rtl.json": {}}))

    prebuilt = PrebuiltAssets(app)
    assert prebuilt.install() is True

    assert prebuilt.asset_maps == {"assets.json": entries, "assets-rtl.json": {}}


def test_an_archive_without_assets_json_entries_keeps_the_file_name_scan(tmp_path: Path, release: Path) -> None:
    app = _app(tmp_path)
    _publish(release, app)

    prebuilt = PrebuiltAssets(app)
    prebuilt.install()

    assert prebuilt.asset_maps is None


def test_assets_json_entries_of_another_app_are_refused(tmp_path: Path, release: Path) -> None:
    app = _app(tmp_path)
    _publish(release, app, _manifest(app, **{"assets.json": {"desk.bundle.js": "/assets/frappe/dist/js/desk.js"}}))

    with pytest.raises(BenchError, match=r"invalid assets\.json"):
        PrebuiltAssets(app).install()


def test_merging_an_app_s_entries_drops_its_stale_keys(tmp_path: Path) -> None:
    from pilot.managers.python_assets import PythonAssetBuilder

    assets_json = tmp_path / "assets.json"
    assets_json.write_text(json.dumps({"old.bundle.js": "/assets/myapp/dist/js/old.js", "desk.bundle.js": "/assets/frappe/x.js"}))

    PythonAssetBuilder.merge_json(assets_json, {"new.bundle.js": "/assets/myapp/dist/js/new.js"}, replacing="/assets/myapp/")

    assert json.loads(assets_json.read_text()) == {
        "desk.bundle.js": "/assets/frappe/x.js",
        "new.bundle.js": "/assets/myapp/dist/js/new.js",
    }


def test_a_frappe_archive_keeps_the_bench_s_page_islands(tmp_path: Path, release: Path) -> None:
    """Frappe builds every app's page islands into its own dist; the archive cannot hold them."""
    app = _app(tmp_path)
    app.config.name = "frappe"
    islands = app.path / "frappe" / "public" / "dist" / "page-island"
    islands.mkdir(parents=True)
    (islands / "crm.page.board.js").write_text("crm island")
    name = f"frappe-{app.installed_hash}.tar.gz"
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        manifest = {"app": "frappe", "commit": app.installed_hash, "paths": ["frappe/public/dist"]}
        for member, content in {
            "manifest.json": json.dumps(manifest),
            "frappe/public/dist/js/desk.bundle.CCCCCCCC.js": "desk",
            "frappe/public/dist/page-island/stale.js": "from CI",
        }.items():
            info = tarfile.TarInfo(member)
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content.encode()))
    (release / name).write_bytes(buffer.getvalue())
    (release / f"{name}.sha256").write_text(f"{hashlib.sha256(buffer.getvalue()).hexdigest()}  {name}\n")

    assert PrebuiltAssets(app).install() is True

    assert sorted(path.name for path in islands.iterdir()) == ["crm.page.board.js"]
    assert (app.path / "frappe" / "public" / "dist" / "js" / "desk.bundle.CCCCCCCC.js").exists()


def test_replacing_frappe_s_entries_keeps_page_island_entries(tmp_path: Path) -> None:
    from pilot.core.app.prebuilt_assets import PAGE_ISLAND_URL
    from pilot.managers.python_assets import PythonAssetBuilder

    assets_json = tmp_path / "assets.json"
    island = {"crm.page.board.js": f"{PAGE_ISLAND_URL}board.js"}
    assets_json.write_text(json.dumps({"desk.bundle.js": "/assets/frappe/dist/js/old.js", **island}))

    PythonAssetBuilder.merge_json(
        assets_json, {"desk.bundle.js": "/assets/frappe/dist/js/new.js"}, replacing="/assets/frappe/", keeping=PAGE_ISLAND_URL
    )

    assert json.loads(assets_json.read_text()) == {"desk.bundle.js": "/assets/frappe/dist/js/new.js", **island}
