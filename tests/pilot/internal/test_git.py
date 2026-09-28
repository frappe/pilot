"""Tests for GitRepo against real, throwaway git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from pilot.exceptions import BenchError
from pilot.internal.git import GitRepo


def _git(path: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


def _init_repo(path: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", branch, str(path)], check=True)
    _git(path, "config", "user.email", "t@t.com")
    _git(path, "config", "user.name", "t")
    return path


def _commit(path: Path, message: str = "init") -> None:
    (path / "file").write_text(message)
    _git(path, "add", "file")
    _git(path, "commit", "-q", "-m", message)


def test_reads_branch_head_and_subject(tmp_path: Path) -> None:
    repo_path = _init_repo(tmp_path / "repo")
    _commit(repo_path, "first commit")
    repo = GitRepo(repo_path)

    assert repo.is_cloned is True
    assert repo.branch == "main"
    assert len(repo.head_sha) == 40
    assert repo.short_head == repo.head_sha[:7]
    assert repo.commit_subject() == "first commit"


def test_missing_repo_degrades_to_empty(tmp_path: Path) -> None:
    repo = GitRepo(tmp_path / "nope")

    assert repo.is_cloned is False
    assert repo.branch == ""
    assert repo.head_sha == ""
    assert repo.commit_subject() == ""
    assert repo.count("HEAD..origin/main") == 0
    assert repo.last_fetched is None


def test_has_local_changes_tracks_working_tree(tmp_path: Path) -> None:
    repo_path = _init_repo(tmp_path / "repo")
    _commit(repo_path)
    repo = GitRepo(repo_path)

    assert repo.has_local_changes is False
    (repo_path / "file").write_text("changed")
    assert repo.has_local_changes is True


def test_set_remote_url_and_remote_url(tmp_path: Path) -> None:
    repo_path = _init_repo(tmp_path / "repo")
    _commit(repo_path)
    _git(repo_path, "remote", "add", "origin", "https://example.com/a.git")
    repo = GitRepo(repo_path)

    assert repo.remote_url == "https://example.com/a.git"
    assert repo.set_remote_url("https://example.com/b.git") is True
    assert repo.remote_url == "https://example.com/b.git"


def test_fetch_and_count_track_new_remote_commits(tmp_path: Path) -> None:
    remote = _init_repo(tmp_path / "remote")
    _commit(remote, "base")
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(remote), str(clone)], check=True)
    _commit(remote, "newer")

    repo = GitRepo(clone)
    assert repo.fetch(repo.branch) is True
    assert repo.count("HEAD..FETCH_HEAD") == 1


def test_has_commit_and_is_ancestor_relate_two_commits(tmp_path: Path) -> None:
    repo_path = _init_repo(tmp_path / "repo")
    _commit(repo_path, "first")
    repo = GitRepo(repo_path)
    first = repo.head_sha
    _commit(repo_path, "second")
    second = repo.head_sha

    assert repo.has_commit(first) is True
    assert repo.has_commit("0" * 40) is False
    assert repo.is_ancestor(first, second) is True
    assert repo.is_ancestor(second, first) is False
    # A commit this clone has never seen can't be related either way.
    assert repo.is_ancestor("0" * 40, second) is False


def test_prune_stale_temp_packs_spares_recent_files_and_real_packs(tmp_path: Path) -> None:
    """Only a killed fetch's leftovers go; a fetch in flight and real packs stay."""
    import os
    import time

    repo_path = _init_repo(tmp_path / "repo")
    _commit(repo_path)
    pack_dir = repo_path / ".git" / "objects" / "pack"
    pack_dir.mkdir(parents=True, exist_ok=True)
    stale = pack_dir / "tmp_pack_abandoned"
    in_flight = pack_dir / "tmp_pack_running"
    real_pack = pack_dir / "pack-1234.pack"
    for path in (stale, in_flight, real_pack):
        path.write_text("x")
    two_days_ago = time.time() - 2 * 24 * 60 * 60
    os.utime(stale, (two_days_ago, two_days_ago))
    os.utime(real_pack, (two_days_ago, two_days_ago))

    GitRepo(repo_path).prune_stale_temp_packs()

    assert not stale.exists()
    assert in_flight.exists()
    assert real_pack.exists()


def test_prune_stale_temp_packs_is_quiet_on_a_missing_repo(tmp_path: Path) -> None:
    GitRepo(tmp_path / "nope").prune_stale_temp_packs()


def test_add_worktree_creates_a_branch_then_reuses_it(tmp_path: Path) -> None:
    repo_path = _init_repo(tmp_path / "repo")
    _commit(repo_path)
    repo = GitRepo(repo_path)

    repo.add_worktree(tmp_path / "one", "feature")
    assert repo.has_branch("feature")
    assert GitRepo(tmp_path / "one").branch == "feature"

    repo.remove_worktree(tmp_path / "one")
    repo.add_worktree(tmp_path / "two", "feature")
    assert GitRepo(tmp_path / "two").branch == "feature"


def test_add_worktree_tracks_a_branch_that_only_origin_has(tmp_path: Path) -> None:
    remote = _init_repo(tmp_path / "remote")
    _commit(remote)
    _git(remote, "checkout", "-q", "-b", "feature")
    _commit(remote, "feature work")
    _git(remote, "checkout", "-q", "main")
    subprocess.run(["git", "clone", "-q", str(remote), str(tmp_path / "clone")], check=True)
    repo = GitRepo(tmp_path / "clone")
    assert not repo.has_branch("feature")

    repo.add_worktree(tmp_path / "wt", "feature")

    worktree = GitRepo(tmp_path / "wt")
    assert worktree.branch == "feature"
    assert worktree.head_sha == repo.tracking_sha("feature")
    upstream = subprocess.run(
        ["git", "-C", str(tmp_path / "wt"), "rev-parse", "--abbrev-ref", "@{upstream}"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert upstream.stdout.strip() == "origin/feature"


def test_remove_worktree_refuses_a_dirty_tree_without_force(tmp_path: Path) -> None:
    repo_path = _init_repo(tmp_path / "repo")
    _commit(repo_path)
    repo = GitRepo(repo_path)
    worktree = tmp_path / "wt"
    repo.add_worktree(worktree, "feature")
    (worktree / "untracked").write_text("x")

    assert GitRepo(worktree).changed_files == ["?? untracked"]
    with pytest.raises(BenchError):
        repo.remove_worktree(worktree)
    assert worktree.exists()

    repo.remove_worktree(worktree, force=True)
    repo.prune_worktrees()
    assert not worktree.exists()


def test_delete_branch_refuses_an_unmerged_branch(tmp_path: Path) -> None:
    repo_path = _init_repo(tmp_path / "repo")
    _commit(repo_path)
    repo = GitRepo(repo_path)
    worktree = tmp_path / "wt"
    repo.add_worktree(worktree, "feature")
    _commit(worktree, "unmerged work")
    repo.remove_worktree(worktree)

    with pytest.raises(BenchError, match="not fully merged"):
        repo.delete_branch("feature")
    assert repo.has_branch("feature")
