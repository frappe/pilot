from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING

from pilot.exceptions import BenchError

if TYPE_CHECKING:
    from pilot.core.app import App

_DOWNLOAD_TIMEOUT_SECONDS = 60
_CHUNK_BYTES = 1024 * 1024


class PrebuiltAssets:
    """Assets the app's CI built for one commit (scripts/build-app-assets.sh), published on
    its GitHub release `assets-<branch>` as `<app>-<commit>.tar.gz` with a `.sha256`."""

    def __init__(self, app: App) -> None:
        self.app = app

    def install(self) -> bool:
        """Swap in the assets built for HEAD. False when none are published or the download
        fails, so the caller builds. A wrong checksum or manifest is an error, not a build."""
        base_url = self.release_url
        commit = self.app.installed_hash
        if not base_url or not commit:
            return False
        name = f"{self.app.config.name}-{commit}.tar.gz"
        try:
            with urllib.request.urlopen(f"{base_url}/{name}.sha256", timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response:
                expected = response.read().decode().split()[0]
        except (urllib.error.URLError, OSError, IndexError) as error:
            print(f"  No prebuilt assets for {self.app.config.name} at {commit[:10]} ({error}).")
            return False

        print(f"  Downloading prebuilt assets for {self.app.config.name} at {commit[:10]}...")
        with tempfile.TemporaryDirectory(dir=self.app.path.parent, prefix=f".{name}-") as work:
            archive = Path(work) / name
            try:
                digest = self._download(f"{base_url}/{name}", archive)
            except (urllib.error.URLError, OSError) as error:
                print(f"  Could not download prebuilt assets ({error}); building instead.")
                return False
            if digest != expected:
                raise BenchError(f"Prebuilt assets for {name} do not match their checksum.")
            self._swap_in(archive, Path(work) / "files", commit)
        return True

    @property
    def release_url(self) -> str | None:
        from pilot.integrations.git.base import GitProviderError
        from pilot.integrations.git.github import parse_github_owner_repo
        from pilot.internal.git import GitRepo

        branch = GitRepo(self.app.path).branch
        try:
            owner, repository = parse_github_owner_repo(self.app.config.repo)
        except GitProviderError:
            return None
        if not branch:
            return None
        tag = f"assets-{branch.replace('/', '-')}"
        return f"https://github.com/{owner}/{repository}/releases/download/{tag}"

    def _swap_in(self, archive: Path, extracted: Path, commit: str) -> None:
        """Replace each published path whole, so files a newer build dropped do not linger."""
        from pilot.utils import extract_tar_archive

        extract_tar_archive(archive, extracted)
        manifest = json.loads((extracted / "manifest.json").read_text())
        if manifest.get("app") != self.app.config.name or manifest.get("commit") != commit:
            raise BenchError(f"Prebuilt assets for {self.app.config.name} were built for another commit.")
        paths = [Path(relative) for relative in manifest.get("paths") or []]
        for path in paths:
            if not path.parts or path.is_absolute() or ".." in path.parts:
                raise BenchError(f"Prebuilt assets name a path outside the app: {str(path)!r}")
            if not (extracted / path).exists():
                raise BenchError(f"Prebuilt assets list {str(path)!r} but do not contain it.")
        for path in paths:
            self._replace(extracted / path, self.app.path / path)

    @staticmethod
    def _replace(source: Path, target: Path) -> None:
        previous = target.with_name(f".{target.name}.previous")
        shutil.rmtree(previous, ignore_errors=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            target.rename(previous)
        try:
            source.rename(target)
        except OSError:
            if previous.exists():
                previous.rename(target)
            raise
        if previous.is_dir():
            shutil.rmtree(previous)
        else:
            previous.unlink(missing_ok=True)

    @staticmethod
    def _download(url: str, destination: Path) -> str:
        digest = hashlib.sha256()
        with urllib.request.urlopen(url, timeout=_DOWNLOAD_TIMEOUT_SECONDS) as response, destination.open("wb") as file:
            while chunk := response.read(_CHUNK_BYTES):
                digest.update(chunk)
                file.write(chunk)
        return digest.hexdigest()
