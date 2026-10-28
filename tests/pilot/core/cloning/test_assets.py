from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from pilot.core.bench import Bench
from pilot.managers.environment import PythonEnvManager

from .git_repository import git


@pytest.mark.parametrize("failure", [PermissionError, OSError])
def test_asset_fingerprint_rejects_unreadable_metadata(tmp_path, monkeypatch, failure):
    from pathlib import Path

    from pilot.core.bench.build_artifacts import BuildArtifacts

    path = tmp_path / "source.js"

    def unreadable(self):
        raise failure("unreadable metadata")

    monkeypatch.setattr(Path, "lstat", unreadable)
    assert BuildArtifacts.get_file_key(path) is None


@pytest.mark.parametrize("entry", ["../../other-bench", "/tmp/outside", "apps/frappe/hooks.py", 123])
def test_asset_cache_rejects_unsafe_manifest_before_copying(source, monkeypatch, entry):
    from pilot.core.bench.artifacts import BenchArtifacts
    from pilot.core.bench.build_artifacts import BuildArtifacts

    artifacts = BuildArtifacts(source)
    cached = artifacts.root / "key"
    (cached / "sites/assets").mkdir(parents=True)
    (cached / "manifest.json").write_text(json.dumps(["sites/assets", entry]))
    copied = []
    monkeypatch.setattr(BenchArtifacts, "restore", lambda *args: copied.append(args))
    assert not artifacts.restore("key")
    assert copied == []


@pytest.mark.parametrize("linked", ["source", "destination"])
def test_asset_cache_rejects_output_symlink_outside_bench(source, tmp_path, linked):
    from pilot.core.bench.build_artifacts import BuildArtifacts

    artifacts = BuildArtifacts(source)
    cached = artifacts.root / "key"
    (cached / "sites/assets").mkdir(parents=True)
    (cached / "manifest.json").write_text(json.dumps(["sites/assets"]))
    outside = tmp_path / "outside"
    outside.mkdir()
    path = (cached if linked == "source" else source.path) / "sites/assets"
    if path.exists():
        path.rmdir()
    path.symlink_to(outside, target_is_directory=True)
    assert not artifacts.restore("key")
    assert list(outside.iterdir()) == []


@pytest.mark.parametrize("copy_fails", [False, True])
def test_python_failure_waits_for_dependency_copy_and_preserves_both_errors(source, monkeypatch, copy_fails):
    from threading import Event

    from pilot.core.bench.cloning.bench import BenchClone

    started, finished = Event(), Event()
    original = RuntimeError("Python install failed")

    def copy(self, destination):
        started.set()
        finished.set()
        if copy_fails:
            raise RuntimeError("dependency copy failed")

    def fail(self):
        assert started.wait(5)
        raise original

    monkeypatch.setattr(BenchClone, "copy_dependencies", copy)
    monkeypatch.setattr(PythonEnvManager, "create_venv", fail)
    with pytest.raises(RuntimeError, match="Python install failed") as caught:
        BenchClone(source, "copy").run(on_progress=lambda message: None)
    assert caught.value is original
    assert finished.is_set()
    if copy_fails:
        assert any("dependency copy failed" in note for note in original.__notes__)


@pytest.mark.parametrize("declaration", ['build_dir = "desk"', 'index_html_path = "frappe/www/custom.html"'])
def test_declared_asset_builds_skip_incomplete_standard_output_cache(source, declaration):
    from pilot.core.bench.build_artifacts import BuildArtifacts

    app = source.app("frappe")
    assert BuildArtifacts.get_app_key(app) is not None
    project = app.path / "pyproject.toml"
    project.write_text(f"[tool.bench.assets]\n{declaration}\n")

    assert BuildArtifacts.get_app_key(app) is None


@pytest.mark.parametrize("failure", ["directory", "copy", "rename"])
def test_asset_cache_failure_keeps_completed_build(source, monkeypatch, caplog, failure):
    from pathlib import Path

    from pilot.core.bench.artifacts import BenchArtifacts
    from pilot.core.bench.build_artifacts import BuildArtifacts
    from pilot.exceptions import CommandError

    assets = source.sites_path / "assets"
    (assets / "app.js").write_text("completed build")
    cache = BuildArtifacts(source)

    def fail(*args, **kwargs):
        if failure == "copy":
            raise CommandError("No space left on device")
        raise PermissionError("Cache is read-only")

    with monkeypatch.context() as patcher:
        if failure == "directory":
            patcher.setattr(Path, "mkdir", fail)
        elif failure == "copy":
            patcher.setattr(BenchArtifacts, "copy_directory", fail)
        else:
            patcher.setattr(Path, "rename", fail)
        cache.capture("build-key")
    assert (assets / "app.js").read_text() == "completed build"
    assert "Skipping asset cache publication" in caplog.text
    assert not (cache.root / "build-key").exists()
    assert not list(cache.root.glob("build-*"))
    cache.capture("build-key")
    assert (cache.root / "build-key/sites/assets/app.js").read_text() == "completed build"


def test_dependency_copy_seeds_changed_lockfile_without_reusing_install_stamp(tmp_path):
    from pilot.managers.node_dependencies import NodeDependencies

    original, copied = tmp_path / "source", tmp_path / "target"
    for directory in (original, copied):
        directory.mkdir()
        (directory / "package.json").write_text("{}")
        (directory / "yarn.lock").write_text("locked dependencies")
    modules = original / "node_modules"
    modules.mkdir()
    (modules / ".yarn-integrity").write_text("{}")
    (modules / "dependency.js").write_text("original")
    assert not NodeDependencies.copy(original, copied)
    (modules / ".pilot-install-key").write_text(NodeDependencies.get_key(original))
    assert NodeDependencies.copy(original, copied)
    (copied / "node_modules/dependency.js").write_text("target change")
    assert (modules / "dependency.js").read_text() == "original"
    (copied / "yarn.lock").write_text("different dependencies")
    assert NodeDependencies.copy(original, copied)
    assert (copied / "node_modules/dependency.js").read_text() == "original"
    assert not (copied / "node_modules/.pilot-install-key").exists()
    assert not NodeDependencies.has_matching_install(copied, NodeDependencies.get_key(copied))
    assert (modules / ".pilot-install-key").exists()
    (copied / ".yarnrc").write_text("ignore-scripts true\n")
    assert not NodeDependencies.copy(original, copied)
    (copied / ".yarnrc").unlink()
    for package in (
        {"scripts": {"postinstall": "custom-build"}},
        {"resolutions": {"dependency": "file:../local"}},
        {"dependencies": {"dependency": "file:../local"}, "resolutions": {"dependency": "1.0"}},
        {"workspaces": ["packages/*"]},
    ):
        (original / "package.json").write_text(json.dumps(package))
        (modules / ".pilot-install-key").write_text(NodeDependencies.get_key(original))
        assert not NodeDependencies.copy(original, copied)


def test_batch_python_install_keeps_destination_paths_and_per_app_dev_extras(tmp_path, monkeypatch):
    bench = Bench.create_at(tmp_path / "destination", "destination")
    bench.config.install_dev_extra = True
    for name, extras in (("first", '[project.optional-dependencies]\ndev = ["pytest"]\n'), ("second", "")):
        path = bench.apps_path / name
        path.mkdir(parents=True)
        (path / "pyproject.toml").write_text(f'[project]\nname = "{name}"\nversion = "1.0"\n{extras}')
    installed = []
    monkeypatch.setattr("pilot.managers.environment.ensure_uv", lambda: "uv")
    monkeypatch.setattr(PythonEnvManager, "_build_env", lambda self: {"BUILD_FLAG": "preserved"})
    monkeypatch.setattr(
        "pilot.managers.environment.run_command", lambda argv, **kwargs: installed.append((argv, kwargs))
    )
    environment = PythonEnvManager(bench)
    apps = [bench.app("first"), bench.app("second")]
    environment.install_apps(apps)
    assert len(installed) == 1
    assert installed[0][0] == [
        "uv",
        "pip",
        "install",
        "--python",
        str(bench.python),
        "-e",
        f"{apps[0].path}[dev]",
        "-e",
        str(apps[1].path),
    ]
    assert installed[0][1]["env"] == {"BUILD_FLAG": "preserved"}
    bench.config.install_dev_extra = False
    environment.install_apps(apps)
    assert installed[-1][0][-4:] == ["-e", str(apps[0].path), "-e", str(apps[1].path)]


def test_build_fingerprint_tracks_link_targets_modes_and_missing_files(source):
    from pilot.core.bench.build_artifacts import BuildArtifacts

    app = source.app("frappe")
    file = app.path / "frappe/__init__.py"
    link = app.path / "linked.py"
    link.symlink_to("frappe/__init__.py")
    key = BuildArtifacts.get_app_key(app)
    file.chmod(file.stat().st_mode | 0o111)
    assert BuildArtifacts.get_app_key(app) != key
    key = BuildArtifacts.get_app_key(app)
    file.write_text("changed content")
    assert BuildArtifacts.get_app_key(app) != key
    file.unlink()
    assert BuildArtifacts.get_app_key(app) is not None
    link.unlink()
    link.symlink_to("linked.py")
    assert BuildArtifacts.get_app_key(app) is None
    link.unlink()
    link.symlink_to("frappe")
    assert BuildArtifacts.get_app_key(app) is None


def test_default_clone_reuses_matching_build_without_sharing_writable_artifacts(source, monkeypatch):
    from pilot.core.bench.build_artifacts import BuildArtifacts

    PythonEnvManager(source).create_venv()
    app = source.apps_path / "frappe"
    dist = app / "frappe/public/dist"
    dist.mkdir(parents=True)
    (dist / "app.js").write_text("built baseline")
    (source.sites_path / "assets/frappe").symlink_to(app / "frappe/public")
    artifacts = BuildArtifacts(source)
    artifacts.capture(artifacts.get_key())
    git(app, "checkout", "-b", "feature")
    (app / "frappe/__init__.py").write_text("feature code")
    builds = []
    monkeypatch.setattr(PythonEnvManager, "build_assets", lambda self: builds.append(self.bench.path))
    destination = source.clone("cached", lambda message: None)
    copied = destination.sites_path / "assets/frappe/dist/app.js"
    assert copied.read_text() == "built baseline"
    assert builds == []
    copied.write_text("destination change")
    assert (dist / "app.js").read_text() == "built baseline"
    assert (app / "frappe/__init__.py").read_text() == "feature code"
    git(app, "checkout", "--", "frappe/__init__.py")
    (app / "frappe/__init__.py").write_text("new remote code")
    git(app, "add", ".")
    git(app, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "update")
    git(app, "push", "origin", "HEAD:main")
    source.clone("changed", lambda message: None)
    assert len(builds) == 1


def test_pnpm_build_cache_does_not_require_yarn(source, monkeypatch):
    from pilot.core.app.node_packages import NodePackageManager
    from pilot.core.bench.build_artifacts import BuildArtifacts

    app = source.apps_path / "frappe"
    (app / "package.json").write_text("{}")
    (app / "yarn.lock").unlink(missing_ok=True)
    (app / "pnpm-lock.yaml").write_text('lockfileVersion: "9.0"\n')
    requested = []

    def binary(manager):
        requested.append(manager.name)
        assert manager.name != "yarn"
        return "pnpm"

    monkeypatch.setattr(NodePackageManager, "bin", property(binary))
    PythonEnvManager(source).create_venv()
    source.write_common_site_config()
    monkeypatch.setattr(
        "pilot.core.bench.build_artifacts.run_command",
        lambda *args, **kwargs: SimpleNamespace(stdout=b"version"),
    )
    assert BuildArtifacts(source).get_key() is not None
    assert requested == ["pnpm"]


def test_build_cache_rejects_changed_build_mode_environment_and_custom_hooks(source, monkeypatch):
    from pilot.core.bench.build_artifacts import BuildArtifacts

    PythonEnvManager(source).create_venv()
    source.write_common_site_config()
    artifacts = BuildArtifacts(source)
    key = artifacts.get_key()
    generated = source.apps_path / "frappe/frappe/public/dist"
    generated.mkdir(parents=True)
    (generated / "bundle.js").write_text("compiled output")
    assert artifacts.get_key() == key
    code = source.apps_path / "frappe/frappe/public/distinct"
    code.mkdir()
    (code / "input.js").write_text("frontend source")
    assert artifacts.get_key() != key
    (code / "input.js").unlink()
    config = source.sites_path / "common_site_config.json"
    original = config.read_text()
    changed = json.loads(original)
    changed["esbuild_target"] = "es2020"
    config.write_text(json.dumps(changed))
    assert artifacts.get_key() != key
    config.write_text(original)
    monkeypatch.setenv("VITE_API_URL", "https://different.example")
    assert artifacts.get_key() != key
    hooks = source.apps_path / "frappe/frappe/hooks.py"
    hooks.write_text("after_build = 'frappe.custom_build'\n")
    assert artifacts.get_key() is None
