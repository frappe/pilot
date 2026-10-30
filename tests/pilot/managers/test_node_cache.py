import json
import shutil
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from pilot.core.app.node_packages import NodePackages
from pilot.exceptions import CommandError
from pilot.managers.node_cache import NodeDependencyCache
from pilot.managers.node_dependencies import NodeDependencies


@pytest.fixture
def install(tmp_path):
    source = tmp_path / "benches/source/apps/frappe"
    source.mkdir(parents=True)
    (source / "package.json").write_text('{"dependencies":{"dependency":"1.0"}}')
    (source / "yarn.lock").write_text("resolved dependencies")
    modules = source / "node_modules"
    (modules / ".bin").mkdir(parents=True)
    (modules / "dependency.js").write_text("installed dependency")
    (modules / ".bin/dependency").symlink_to(modules / "dependency.js")
    (modules / ".yarn-integrity").write_text('{"lockfileEntries":{"dependency":"resolved"}}')
    target = tmp_path / "benches/target/apps/frappe"
    target.mkdir(parents=True)
    for name in ("package.json", "yarn.lock"):
        shutil.copy2(source / name, target / name)
    cache = NodeDependencyCache(SimpleNamespace(path=tmp_path / "benches/target"))
    return source, target, cache


def capture_install(source, cache):
    (source / "node_modules/.pilot-install-key").write_text(NodeDependencies.get_key(source))
    cache.capture(source)


def test_cache_restores_between_distinct_task_processes(install, monkeypatch):
    source, target, cache = install
    capture_install(source, cache)
    for key in ("BENCH_TASK_ID", "BENCH_TASK_LAUNCH_ID", "BENCH_TASK_SECRETS_FILE", "BENCH_TASK_READY_FD"):
        monkeypatch.setenv(key, "first-task")
    monkeypatch.setenv("PILOT_NONINTERACTIVE_PRIVILEGES", "1")
    assert NodeDependencies.has_matching_install(source, NodeDependencies.get_key(source))
    for key in ("BENCH_TASK_ID", "BENCH_TASK_LAUNCH_ID", "BENCH_TASK_SECRETS_FILE", "BENCH_TASK_READY_FD"):
        monkeypatch.setenv(key, "second-task")
    assert cache.restore(target)
    assert NodeDependencies.has_matching_install(target, NodeDependencies.get_key(target))


@pytest.mark.parametrize("target_kind", ["external", "dangling", "loop", "modules"])
def test_dependency_copy_rejects_linked_installs(install, target_kind):
    source, target, _cache = install
    modules = source / "node_modules"
    if target_kind == "modules":
        moved = source / "shared-modules"
        modules.rename(moved)
        modules.symlink_to(moved, target_is_directory=True)
    else:
        targets = {
            "external": source / "package.json",
            "dangling": source / "missing",
            "loop": modules / "linked",
        }
        (modules / "linked").symlink_to(targets[target_kind])
    (modules / ".pilot-install-key").write_text(NodeDependencies.get_key(source))
    assert not NodeDependencies.copy(source, target)
    assert not (target / "node_modules").exists()


@pytest.mark.parametrize("failure", ["directory", "copy", "rename"])
def test_cache_storage_failure_preserves_completed_install(install, monkeypatch, caplog, failure):
    from pathlib import Path

    from pilot.core.bench.artifacts import BenchArtifacts

    source, target, cache = install
    key = NodeDependencies.get_key(source)
    (source / "node_modules/.pilot-install-key").write_text(key)

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
        cache.capture(source)
    assert "Skipping Node dependency cache publication" in caplog.text
    assert NodeDependencies.has_matching_install(source, key)
    assert not (cache.root / key).exists()
    assert not list(cache.root.glob("install-*"))
    cache.capture(source)
    assert cache.restore(target)


def test_cached_install_survives_source_removal_and_skips_yarn(install):
    source, target, cache = install
    capture_install(source, cache)
    shutil.rmtree(source)
    assert cache.restore(target)
    link = target / "node_modules/.bin/dependency"
    assert link.resolve() == target / "node_modules/dependency.js"
    with patch("pilot.core.app.node_packages.run_command") as yarn:
        NodePackages(target).ensure_installed()
    yarn.assert_not_called()
    link.write_text("destination edit")
    entry = cache.root / NodeDependencies.get_key(target)
    assert (entry / "node_modules/dependency.js").read_text() == "installed dependency"


@pytest.mark.parametrize("changed", ["package.json", "yarn.lock", "environment", "ancestor-config"])
def test_changed_install_inputs_do_not_restore_cache(install, monkeypatch, changed):
    source, target, cache = install
    capture_install(source, cache)
    if changed == "environment":
        monkeypatch.setenv("NODE_OPTIONS", "--max-old-space-size=1024")
    elif changed == "ancestor-config":
        (target.parents[1] / ".yarnrc").write_text("--ignore-optional true\n")
    else:
        file = target / changed
        file.write_text(file.read_text() + "\n")
    assert not cache.restore(target)
    assert not (target / "node_modules").exists()


@pytest.mark.parametrize("unsafe", ["hook", "external-link", "link-loop", "corrupt-integrity"])
def test_unsafe_or_corrupt_installs_are_not_reused(install, unsafe):
    source, target, cache = install
    if unsafe == "hook":
        package = json.loads((source / "package.json").read_text())
        package["scripts"] = {"postinstall": "node build.js"}
        for path in (source, target):
            (path / "package.json").write_text(json.dumps(package))
    elif unsafe == "external-link":
        (source / "node_modules/external").symlink_to(source / "package.json")
    elif unsafe == "link-loop":
        (source / "node_modules/loop").symlink_to("loop")
    capture_install(source, cache)
    if unsafe == "corrupt-integrity":
        entry = cache.root / NodeDependencies.get_key(target)
        (entry / "node_modules/.yarn-integrity").write_text("{truncated")
    assert not cache.restore(target)


@pytest.mark.parametrize("branch", ["default", "current"])
def test_cache_hit_precedes_seeding_except_for_current_checkout(install, branch):
    from pilot.core.bench.cloning.bench import BenchClone

    source, target, cache = install
    capture_install(source, cache)
    (source / "package.json").write_text('{"dependencies":{"dependency":"2.0"}}')
    app = SimpleNamespace(path=source, config=SimpleNamespace(name="frappe"))
    source_bench = SimpleNamespace(path=source.parents[1], apps=lambda: [app])
    destination = SimpleNamespace(path=target.parents[1], apps_path=target.parent)
    clone = BenchClone.__new__(BenchClone)
    clone.source = source_bench
    clone.branch = branch
    clone.app_branches = {}
    with patch.object(NodeDependencies, "copy", return_value=False) as seed:
        clone.copy_dependencies(destination)
    assert NodeDependencies.has_matching_install(target, NodeDependencies.get_key(target)) == (
        branch == "default"
    )
    assert any(call.args[1] == target for call in seed.call_args_list) == (branch == "current")
