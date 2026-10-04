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
