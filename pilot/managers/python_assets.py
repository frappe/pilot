from __future__ import annotations

import contextlib
import json
import logging
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.utils import extract_tar_archive, get_yarn_bin, git_has_local_changes, run_command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.managers.environment import PythonEnvManager

_BUNDLE_RE = re.compile(r"^(.+)\.bundle\.[A-Z0-9]{8}\.(js|css)$")


class PythonAssetBuilder:
    def __init__(self, manager: "PythonEnvManager") -> None:
        self.manager = manager
        self.bench = manager.bench

    def build_assets(self) -> None:
        for app in self.bench.apps():
            if (app.path / "package.json").exists():
                self.ensure_yarn_install(app.path)
        run_command(
            [*self.bench.frappe_call, "frappe", "build", "--force"],
            cwd=self.bench.sites_path,
            env=self.manager._build_env(),
            stream_output=True,
        )

    def build_assets_for_app(self, app: "App") -> None:
        app_public_dir = app.path / app.config.name / "public"
        dist_dir = app_public_dir / "dist"

        if not git_has_local_changes(app.path):
            if self.try_download_prebuilt_assets(app, app_public_dir, dist_dir):
                return
            if self.has_prebuilt_assets(dist_dir):
                self.setup_prebuilt_assets(app.config.name, app_public_dir, dist_dir)
                return

        if (app.path / "package.json").exists():
            self.ensure_yarn_install(app.path)

        print(f"  Building assets for {app.config.name}...")
        sys.stdout.flush()
        run_command(
            [*self.bench.frappe_call, "frappe", "build", "--force", "--app", app.config.name],
            cwd=self.bench.sites_path,
            env=self.manager._build_env(),
            stream_output=True,
        )

        for frontend_dir in ["frontend", "roster"]:
            if (app.path / frontend_dir / "package.json").exists():
                print(f"  Building {frontend_dir} for {app.config.name}...")
                sys.stdout.flush()
                run_command(
                    [get_yarn_bin(), "build"],
                    cwd=app.path / frontend_dir,
                    stream_output=True,
                )

    def ensure_yarn_install(self, path: Path) -> None:
        """Run yarn install when node_modules is missing or yarn.lock changed."""
        integrity = path / "node_modules" / ".yarn-integrity"
        lock = path / "yarn.lock"
        if integrity.exists() and (not lock.exists() or lock.stat().st_mtime <= integrity.stat().st_mtime):
            return
        app_name = path.name
        print(f"  Installing JS dependencies for {app_name}...")
        sys.stdout.flush()
        run_command(
            [get_yarn_bin(), "install", "--frozen-lockfile"],
            cwd=path,
            stream_output=True,
        )

    def try_download_prebuilt_assets(
        self,
        app: "App",
        app_public_dir: Path,
        dist_dir: Path,
    ) -> bool:
        from pilot.internal.git import GitRepo

        branch = GitRepo(app.path).branch
        if not branch:
            return False
        url = self.release_asset_url(app, branch)
        if not url:
            return False
        print(f"  Downloading pre-built assets for {app.config.name}...")
        sys.stdout.flush()
        if not self.download_and_extract(url, app_public_dir):
            return False
        self.setup_prebuilt_assets(app.config.name, app_public_dir, dist_dir)
        return True

    @staticmethod
    def release_asset_url(app: "App", branch: str) -> str | None:
        import subprocess

        r = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True,
            text=True,
            cwd=app.path,
        )
        if r.returncode != 0:
            return None
        m = re.search(r"github\.com[:/](.+?)(?:\.git)?$", r.stdout.strip())
        if not m:
            return None
        owner_repo = m.group(1)
        tag = f"assets-{branch.replace('/', '-')}"
        return f"https://github.com/{owner_repo}/releases/download/{tag}/{app.config.name}-assets.tar.gz"

    @staticmethod
    def download_and_extract(url: str, dest_dir: Path) -> bool:
        import tempfile
        import urllib.error
        import urllib.request

        with tempfile.NamedTemporaryFile(suffix=".tar.gz", delete=False) as tmp_file:
            tmp_path = Path(tmp_file.name)
        try:
            urllib.request.urlretrieve(url, tmp_path)
        except urllib.error.URLError:
            tmp_path.unlink(missing_ok=True)
            return False

        try:
            dest_dir.mkdir(parents=True, exist_ok=True)
            extract_tar_archive(tmp_path, dest_dir)
            return True
        except Exception as exc:
            logging.debug("Failed to extract downloaded archive to %s: %s", dest_dir, exc)
            return False
        finally:
            tmp_path.unlink(missing_ok=True)

    @staticmethod
    def has_prebuilt_assets(dist_dir: Path) -> bool:
        js_dir = dist_dir / "js"
        return js_dir.is_dir() and any(_BUNDLE_RE.match(f.name) for f in js_dir.iterdir())

    def setup_prebuilt_assets(self, app_name: str, app_public_dir: Path, dist_dir: Path) -> None:
        assets_dir = self.bench.sites_path / "assets"
        assets_dir.mkdir(exist_ok=True)

        app_link = assets_dir / app_name
        if app_link.is_symlink():
            app_link.unlink()
        elif app_link.is_dir():
            shutil.rmtree(str(app_link))
        app_link.symlink_to(app_public_dir.resolve())

        self.write_assets_json(app_name, dist_dir, assets_dir)
        print(f"  Linked {app_link} -> {app_public_dir.resolve()}")

    def write_assets_json(self, app_name: str, dist_dir: Path, assets_dir: Path) -> None:
        assets = {
            **self._bundle_manifest(app_name, dist_dir, "js", "js"),
            **self._bundle_manifest(app_name, dist_dir, "css", "css"),
        }
        rtl_assets = self._bundle_manifest(
            app_name,
            dist_dir,
            "css-rtl",
            "css",
            key_prefix="rtl_",
        )

        self.merge_json(assets_dir / "assets.json", assets)
        if rtl_assets:
            self.merge_json(assets_dir / "assets-rtl.json", rtl_assets)

    @staticmethod
    def _bundle_manifest(
        app_name: str,
        dist_dir: Path,
        directory: str,
        extension: str,
        *,
        key_prefix: str = "",
    ) -> dict[str, str]:
        bundle_dir = dist_dir / directory
        if not bundle_dir.is_dir():
            return {}

        entries = {}
        for path in sorted(bundle_dir.iterdir()):
            match = _BUNDLE_RE.match(path.name)
            if match and match.group(2) == extension:
                key = f"{key_prefix}{match.group(1)}.bundle.{extension}"
                entries[key] = f"/assets/{app_name}/dist/{directory}/{path.name}"
        return entries

    @staticmethod
    def merge_json(path: Path, new_entries: dict) -> None:
        existing: dict = {}
        if path.exists():
            with contextlib.suppress(json.JSONDecodeError):
                existing = json.loads(path.read_text())
        existing.update(new_entries)
        path.write_text(json.dumps(existing, indent="\t", sort_keys=True) + "\n")
