from __future__ import annotations

import json

from pilot.core.bench import Bench
from pilot.managers.environment import PythonEnvManager

from .git_repository import git


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
    assert BuildArtifacts.get_app_key(app) is not None
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
