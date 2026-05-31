from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from bench_cli.exceptions import BenchError
from bench_cli.platform import is_macos
from bench_cli.utils import run_command

if TYPE_CHECKING:
    from bench_cli.core.app import App
    from bench_cli.core.bench import Bench

# Matches esbuild content-hash output names: name.bundle.XXXXXXXX.js / .css
_BUNDLE_RE = re.compile(r"^(.+)\.bundle\.[A-Z0-9]{8}\.(js|css)$")


class PythonEnvManager:
    def __init__(self, bench: "Bench") -> None:
        self.bench = bench

    def ensure_python(self) -> None:
        # uv manages Python discovery and download at venv-creation time.
        pass

    def create_venv(self) -> None:
        if self.bench.python.exists():
            return
        uv = self._ensure_uv()
        version = self.bench.config.python_version
        run_command([uv, "venv", "--python", version, str(self.bench.env_path)], stream_output=True)

    def install_app(self, app: "App") -> None:
        uv = self._ensure_uv()
        python = str(self.bench.env_path / "bin" / "python")
        run_command([uv, "pip", "install", "--python", python, "-e", str(app.path)], stream_output=True)

    def uninstall_app(self, app_name: str) -> None:
        uv = self._ensure_uv()
        python = str(self.bench.env_path / "bin" / "python")
        run_command([uv, "pip", "uninstall", "--python", python, app_name], stream_output=True)

    def install_node(self) -> None:
        if shutil.which("node"):
            if not shutil.which("yarn"):
                run_command(["npm", "install", "-g", "yarn"])
            return
        if is_macos():
            run_command(["brew", "install", "node"])
        else:
            self._install_node_linux()
        run_command(["npm", "install", "-g", "yarn"])

    def install_node_dependencies(self) -> None:
        for app in self.bench.apps():
            if (app.path / "package.json").exists():
                run_command(["npm", "install"], cwd=app.path, stream_output=True)

    def build_assets(self) -> None:
        run_command(
            [*self.bench.frappe_call, "frappe", "build", "--force"],
            cwd=self.bench.sites_path, stream_output=True,
        )

    def build_assets_for_app(self, app: "App") -> None:
        """Build or link assets for one app.

        If the app ships pre-built bundles in dist/ (committed to git),
        wire them up without running yarn or esbuild. Otherwise fall back
        to a full yarn install + frappe build.
        """
        app_dir = app.path  # apps/{name}/
        # Frappe apps keep their Python package (and public/) one level in:
        # apps/{name}/{name}/public/
        app_public_dir = app_dir / app.config.name / "public"
        dist_dir = app_public_dir / "dist"

        if self._has_prebuilt_assets(dist_dir):
            print(f"  Pre-built assets found for {app.config.name} — linking without rebuild...")
            sys.stdout.flush()
            self._setup_prebuilt_assets(app.config.name, app_public_dir, dist_dir)
            return

        if (app_dir / "package.json").exists():
            print(f"  Installing JS dependencies for {app.config.name}...")
            sys.stdout.flush()
            run_command(["yarn", "install"], cwd=app_dir, stream_output=True)

        print(f"  Building assets for {app.config.name}...")
        sys.stdout.flush()
        run_command(
            [*self.bench.frappe_call, "frappe", "build", "--force"],
            cwd=self.bench.sites_path,
            stream_output=True,
        )

    # ------------------------------------------------------------------
    # Pre-built asset helpers
    # ------------------------------------------------------------------

    def _has_prebuilt_assets(self, dist_dir: Path) -> bool:
        js_dir = dist_dir / "js"
        return js_dir.is_dir() and any(
            _BUNDLE_RE.match(f.name) for f in js_dir.iterdir()
        )

    def _setup_prebuilt_assets(
        self, app_name: str, app_public_dir: Path, dist_dir: Path
    ) -> None:
        """Symlink public/ into sites/assets/ and generate assets.json files."""
        assets_dir = self.bench.sites_path / "assets"
        assets_dir.mkdir(exist_ok=True)

        # sites/assets/{app}/ -> apps/{app}/{app}/public/
        app_link = assets_dir / app_name
        if app_link.is_symlink():
            app_link.unlink()
        elif app_link.is_dir():
            shutil.rmtree(str(app_link))
        app_link.symlink_to(app_public_dir.resolve())

        self._write_assets_json(app_name, dist_dir, assets_dir)
        print(f"  Linked {app_link} -> {app_public_dir.resolve()}")

    def _write_assets_json(
        self, app_name: str, dist_dir: Path, assets_dir: Path
    ) -> None:
        assets: dict[str, str] = {}
        rtl_assets: dict[str, str] = {}

        js_dir = dist_dir / "js"
        if js_dir.is_dir():
            for f in sorted(js_dir.iterdir()):
                m = _BUNDLE_RE.match(f.name)
                if m and m.group(2) == "js":
                    assets[f"{m.group(1)}.bundle.js"] = (
                        f"/assets/{app_name}/dist/js/{f.name}"
                    )

        css_dir = dist_dir / "css"
        if css_dir.is_dir():
            for f in sorted(css_dir.iterdir()):
                m = _BUNDLE_RE.match(f.name)
                if m and m.group(2) == "css":
                    assets[f"{m.group(1)}.bundle.css"] = (
                        f"/assets/{app_name}/dist/css/{f.name}"
                    )

        rtl_dir = dist_dir / "css-rtl"
        if rtl_dir.is_dir():
            for f in sorted(rtl_dir.iterdir()):
                m = _BUNDLE_RE.match(f.name)
                if m and m.group(2) == "css":
                    rtl_assets[f"rtl_{m.group(1)}.bundle.css"] = (
                        f"/assets/{app_name}/dist/css-rtl/{f.name}"
                    )

        self._merge_json(assets_dir / "assets.json", assets)
        if rtl_assets:
            self._merge_json(assets_dir / "assets-rtl.json", rtl_assets)

    @staticmethod
    def _merge_json(path: Path, new_entries: dict) -> None:
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text())
            except json.JSONDecodeError:
                pass
        existing.update(new_entries)
        path.write_text(json.dumps(existing, indent="\t", sort_keys=True) + "\n")

    def _ensure_uv(self) -> str:
        """Return path to uv, installing it if not on PATH."""
        uv = shutil.which("uv")
        if uv:
            return uv

        print("uv not found — installing via official installer...", flush=True)
        try:
            run_command(
                ["sh", "-c", "curl -LsSf https://astral.sh/uv/install.sh | sh"],
                stream_output=True,
            )
        except Exception:
            print("curl installer failed — falling back to pip install uv...", flush=True)
            run_command(
                [sys.executable, "-m", "pip", "install", "--user", "uv"],
                stream_output=True,
            )

        # The installer typically puts uv in ~/.local/bin (Linux/macOS).
        for candidate in [
            Path.home() / ".local" / "bin" / "uv",
            Path.home() / ".cargo" / "bin" / "uv",
        ]:
            if candidate.exists():
                return str(candidate)

        # Re-check PATH in case the shell profile was updated.
        uv = shutil.which("uv")
        if uv:
            return uv

        raise BenchError(
            "uv was installed but cannot be found. "
            "Add ~/.local/bin to your PATH and re-run."
        )

    def _install_node_linux(self) -> None:
        run_command(
            ["bash", "-c", "curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash -"],
            stream_output=True,
        )
        run_command(["sudo", "apt-get", "install", "-y", "nodejs"], stream_output=True)
