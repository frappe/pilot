from __future__ import annotations

import subprocess
from threading import Barrier

import pytest

from admin.backend.internal.session import Session
from pilot.config import AppConfig
from pilot.core.bench.cloning.bench import BenchClone
from pilot.core.site.clone import SiteClone
from pilot.exceptions import BenchError, CommandError
from pilot.managers.environment import PythonEnvManager

from .git_repository import git


def test_current_copy_dissociates_borrowed_history_and_preserves_index(source):
    import shutil

    app = source.apps_path / "frappe"
    borrowed = source.path.parent / "borrowed"
    subprocess.run(["git", "clone", "--shared", str(app), str(borrowed)], check=True, capture_output=True)
    file = borrowed / "frappe/__init__.py"
    file.write_text("staged change\n")
    git(borrowed, "add", ".")
    file.write_text("unstaged change\n")
    staged = git(borrowed, "diff", "--cached")
    dirty = git(borrowed, "diff")
    destination = source.path.parent / "independent"
    BenchClone.copy_app(borrowed, destination)
    shutil.rmtree(app)
    shutil.rmtree(borrowed)
    assert not (destination / ".git/objects/info/alternates").exists()
    git(destination, "fsck", "--full")
    assert git(destination, "diff", "--cached") == staged
    assert git(destination, "diff") == dirty


@pytest.mark.parametrize("checkout", ["branch", "detached", "worktree"])
def test_reflink_repository_is_independent_with_fresh_metadata(source, monkeypatch, checkout):
    import shutil

    from pilot.core.bench.cloning import repository as clone_repository

    app = source.apps_path / "frappe"
    git(app, "tag", "baseline")
    git(app, "gc")
    (app / ".git/hooks/post-checkout").write_text("source hook")
    original = app
    if checkout == "detached":
        git(app, "checkout", "--detach")
    elif checkout == "worktree":
        app = source.path.parent / "linked"
        git(original, "worktree", "add", "-b", "task", str(app))
    revision = git(app, "rev-parse", "HEAD")
    run = clone_repository.run_command
    copies = []
    commands = []

    def copy(argv, **kwargs):
        commands.append(argv)
        if argv[0] == "cp":
            copies.append(argv)
            argv = ["--reflink=auto" if part == "--reflink=always" else part for part in argv]
        return run(argv, **kwargs)

    monkeypatch.setattr(clone_repository, "run_command", copy)
    destination = source.path.parent / "independent"
    clone_repository.clone_repository(app, destination)
    assert copies
    assert not any(argv[:2] == ["git", "clone"] for argv in commands)
    assert git(destination, "rev-parse", "HEAD") == revision
    assert git(destination, "rev-parse", "baseline") == revision
    if checkout != "detached":
        assert git(destination, "rev-parse", "--abbrev-ref", "@{upstream}") == (
            "origin/task" if checkout == "worktree" else "origin/main"
        )
    assert not (destination / ".git/hooks/post-checkout").exists()
    assert not (destination / ".git/objects/info/alternates").exists()
    pack = next((original / ".git/objects/pack").glob("*.pack"))
    copied = destination / ".git/objects/pack" / pack.name
    assert (pack.stat().st_dev, pack.stat().st_ino) != (copied.stat().st_dev, copied.stat().st_ino)
    shutil.rmtree(original)
    git(destination, "fsck", "--full")
    git(destination, "checkout", "--force", "HEAD")
    assert (destination / "frappe/__init__.py").read_text() == "VALUE = 'baseline'\n"


def test_alternate_backed_repository_clone_dissociates(source):
    import shutil

    from pilot.core.bench.cloning.repository import clone_repository

    app = source.apps_path / "frappe"
    alternate = source.path.parent / "alternate"
    subprocess.run(["git", "clone", "--shared", str(app), str(alternate)], check=True, capture_output=True)
    assert [
        path.relative_to(alternate / ".git/objects").as_posix()
        for path in (alternate / ".git/objects").rglob("*")
        if path.is_file()
    ] == ["info/alternates"]
    destination = source.path.parent / "independent"
    clone_repository(alternate, destination)
    assert not (destination / ".git/objects/info/alternates").exists()
    shutil.rmtree(app)
    shutil.rmtree(alternate)
    git(destination, "fsck", "--full")


def test_failed_reflink_copy_discards_partial_objects_before_clone(source, monkeypatch):
    from pathlib import Path

    from pilot.core.bench.cloning import repository as clone_repository

    run = clone_repository.run_command

    def copy(argv, **kwargs):
        if argv[0] == "cp":
            (Path(argv[-1]) / "partial").write_text("incomplete object")
            raise CommandError("Reflink unavailable")
        return run(argv, **kwargs)

    monkeypatch.setattr(clone_repository, "run_command", copy)
    destination = source.path.parent / "fallback"
    clone_repository.clone_repository(source.apps_path / "frappe", destination)
    git(destination, "fsck", "--full")
    assert not (destination / ".git/objects/partial").exists()
    assert not list(destination.parent.glob(".pilot-git-*"))


def test_bench_clone_preserves_dirty_files_but_has_independent_git_and_ports(source):
    app = source.apps_path / "frappe"
    (app / "frappe/__init__.py").write_text("VALUE = 'dirty'\n")
    (app / "untracked.txt").write_text("local change")
    (source.sites_path / "assets/frappe").symlink_to(app / "frappe")
    destination = source.clone("uat", lambda message: None, branch="current")
    copied = destination.apps_path / "frappe"
    assert destination.sites() == []
    assert destination.config.http_port != source.config.http_port
    assert Session(destination).ensure_jwt_secret() != Session(source).ensure_jwt_secret()
    assert git(copied, "status", "--porcelain") == git(app, "status", "--porcelain")
    assert (destination.sites_path / "assets/frappe").resolve() == copied / "frappe"
    (copied / "frappe/__init__.py").write_text("destination change")
    assert "dirty" in (app / "frappe/__init__.py").read_text()
    git(copied, "branch", "destination-only")
    assert "destination-only" not in git(app, "branch")
    assert (copied / ".git/HEAD").stat().st_ino != (app / ".git/HEAD").stat().st_ino


def test_default_clone_uses_live_origin_default_and_excludes_feature_changes(source, monkeypatch):
    default_branch = "trunk"
    app = source.apps_path / "frappe"
    remote = git(app, "remote", "get-url", "origin")
    if default_branch != "main":
        git(app, "branch", "-m", default_branch)
    git(app, "push", "origin", default_branch)
    subprocess.run(
        ["git", "--git-dir", remote, "symbolic-ref", "HEAD", f"refs/heads/{default_branch}"], check=True
    )
    upstream = source.path.parent / "upstream"
    subprocess.run(["git", "clone", remote, str(upstream)], check=True, capture_output=True)
    (upstream / "upstream-only.txt").write_text("latest remote commit")
    git(upstream, "add", ".")
    git(
        upstream,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.com",
        "commit",
        "-m",
        "remote advance",
    )
    git(upstream, "push", "origin", default_branch)
    git(app, "checkout", "-b", "feature")
    (app / "feature.txt").write_text("feature commit")
    git(app, "add", ".")
    git(app, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "feature")
    (app / "frappe/__init__.py").write_text("dirty feature")
    (app / "untracked.txt").write_text("untracked")
    before = git(app, "status", "--porcelain")
    builds = []
    monkeypatch.setattr(PythonEnvManager, "build_assets", lambda self: builds.append(self.bench.path))
    destination = source.clone("default-copy", lambda message: None)
    copied = destination.apps_path / "frappe"
    assert git(copied, "branch", "--show-current") == default_branch
    assert git(copied, "status", "--porcelain") == ""
    assert (copied / "frappe/__init__.py").read_text() == "VALUE = 'baseline'\n"
    assert not (copied / "feature.txt").exists()
    assert not (copied / "untracked.txt").exists()
    assert (copied / "upstream-only.txt").read_text() == "latest remote commit"
    assert not (app / "upstream-only.txt").exists()
    assert git(app, "status", "--porcelain") == before
    assert git(app, "branch", "--show-current") == "feature"
    assert builds == [destination.path]
    assert destination.config.apps[0].branch == default_branch


def test_per_app_branch_override_is_fetched_and_checked_out_cleanly(source):
    app = source.apps_path / "frappe"
    git(app, "checkout", "-b", "feature")
    (app / "feature.txt").write_text("feature")
    git(app, "add", ".")
    git(app, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "feature")
    git(app, "push", "origin", "feature")
    (app / "feature.txt").write_text("dirty")
    destination = source.clone("override", lambda message: None, app_branches={"frappe": "feature"})
    assert (destination.apps_path / "frappe/feature.txt").read_text() == "feature"
    assert destination.config.apps[0].branch == "feature"


def test_default_requires_origin_and_unknown_overrides_fail(source):
    git(source.apps_path / "frappe", "remote", "remove", "origin")
    with pytest.raises(BenchError, match="no origin"):
        source.clone("no-origin", lambda message: None)
    with pytest.raises(BenchError, match="Unknown app"):
        source.clone("unknown", app_branches={"missing": "main"})


def test_clone_branch_options_list_origin_branches_with_default_first(source):
    app = source.apps_path / "frappe"
    git(app, "branch", "feature/test")
    git(app, "push", "origin", "feature/test")
    git(app, "branch", "local-only")
    before = git(app, "status", "--porcelain")
    assert source.get_clone_branch_options() == [
        {"name": "frappe", "default_branch": "main", "branches": ["main", "feature/test"]}
    ]
    assert git(app, "status", "--porcelain") == before


def test_clone_branch_options_fail_when_origin_is_unavailable(source):
    git(source.apps_path / "frappe", "remote", "set-url", "origin", "/missing-pilot-test-origin")
    with pytest.raises(BenchError, match="Could not read branches for frappe"):
        source.get_clone_branch_options()


@pytest.mark.parametrize("staged", [False, True])
def test_copying_linked_worktree_materializes_repository(tmp_path, staged):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "marker").write_text("baseline")
    git(repo, "init", "-b", "main")
    git(repo, "add", ".")
    git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "baseline")
    worktree = tmp_path / "worktree"
    git(repo, "worktree", "add", "-b", "task", str(worktree))
    (worktree / "marker").write_text("dirty")
    if staged:
        git(worktree, "add", "marker")
    destination = tmp_path / "copy"
    BenchClone.copy_app(worktree, destination)
    assert (destination / ".git").is_dir()
    assert git(destination, "status", "--porcelain") == git(worktree, "status", "--porcelain")
    git(repo, "worktree", "remove", "--force", str(worktree))
    assert git(destination, "rev-parse", "HEAD") == git(repo, "rev-parse", "HEAD")
    assert (destination / "marker").read_text() == "dirty"


def test_clone_rejects_existing_destinations_and_missing_apps(source):
    with pytest.raises(BenchError, match="already exists"):
        source.clone("dev")
    with pytest.raises(BenchError, match="already exists"):
        SiteClone(source.site("source.localhost"), source, "source.localhost", "admin").validate()
    destination = source.clone("empty", lambda message: None)
    (destination.sites_path / "apps.txt").write_text("")
    with pytest.raises(BenchError, match="not installed"):
        SiteClone(source.site("source.localhost"), destination, "uat.localhost", "admin").validate()


def test_current_clone_does_not_inherit_source_worktrees(source):
    from pilot.internal.git import GitRepo

    app = source.apps_path / "frappe"
    worktree = source.path.parent / "task"
    git(app, "worktree", "add", "-b", "task", str(worktree))
    destination = source.clone("independent", lambda message: None, branch="current")
    copied = destination.apps_path / "frappe"
    assert str(worktree) not in git(copied, "worktree", "list", "--porcelain")
    GitRepo(copied).ensure_removable()
    assert str(worktree) in git(app, "worktree", "list", "--porcelain")
    assert git(worktree, "rev-parse", "--git-common-dir") == str(app / ".git")


@pytest.mark.parametrize("failure", [False, True])
def test_repository_copies_overlap_and_finish_before_return(source, monkeypatch, failure):
    for name in ("addon_a", "addon_b"):
        path = source.apps_path / name
        (path / name).mkdir(parents=True)
        (path / name / "hooks.py").write_text("")
        git(path, "init", "-b", "develop")
        git(path, "add", ".")
        git(path, "-c", "user.name=Test", "-c", "user.email=test@example.com", "commit", "-m", "baseline")
        remote = source.path.parent / f"{name}.git"
        subprocess.run(["git", "clone", "--bare", str(path), str(remote)], check=True, capture_output=True)
        git(path, "remote", "add", "origin", str(remote))
        source.config.apps.append(AppConfig(name=name, repo=str(remote), branch="develop"))
    source.config.write(source.path)
    source.write_apps_txt()
    original = BenchClone.copy_selected_app
    started = Barrier(3)
    completed = set()

    def copy(self, app, destination):
        try:
            started.wait(timeout=5)
            if failure and app.config.name == "addon_a":
                raise RuntimeError("repository failed")
            return original(self, app, destination)
        finally:
            completed.add(app.config.name)

    monkeypatch.setattr(BenchClone, "copy_selected_app", copy)
    if failure:
        with pytest.raises(RuntimeError, match="repository failed"):
            source.clone("parallel-repositories", lambda message: None)
    else:
        destination = source.clone("parallel-repositories", lambda message: None)
        assert {app.name: app.branch for app in destination.config.apps} == {
            "frappe": "main",
            "addon_a": "develop",
            "addon_b": "develop",
        }
    assert completed == {"frappe", "addon_a", "addon_b"}


def test_branch_discovery_and_fetch_use_source_credentials(source, monkeypatch):
    from pilot.core.bench.cloning import branches as clone_branches
    from pilot.integrations.git import auth_config_for
    from pilot.integrations.git.credentials import GitCredentialStore
    from pilot.internal.git import git_env

    app = source.apps_path / "frappe"
    local_origin = git(app, "remote", "get-url", "origin")
    source.config.apps[0].repo = "https://github.com/example/private-app.git"
    git(app, "remote", "set-url", "origin", source.config.apps[0].repo)
    source.config.write(source.path)
    GitCredentialStore(source.path).save("github", "test-private-token")
    expected = git_env(auth_config_for(source.path, source.config.apps[0].repo))
    original = clone_branches.run_command
    authenticated = []

    def run(argv, **kwargs):
        if "ls-remote" in argv or "fetch" in argv:
            authenticated.append(argv)
            for key, value in expected.items():
                if key.startswith("GIT_CONFIG_"):
                    assert kwargs["env"][key] == value
            argv = [local_origin if value == "origin" else value for value in argv]
        return original(argv, **kwargs)

    monkeypatch.setattr(clone_branches, "run_command", run)
    assert source.get_clone_branch_options()[0]["default_branch"] == "main"
    destination = source.clone("authenticated", lambda message: None)
    assert sum("ls-remote" in argv for argv in authenticated) == 2
    assert sum("fetch" in argv for argv in authenticated) == 1
    assert "test-private-token" not in (destination.apps_path / "frappe/.git/config").read_text()
