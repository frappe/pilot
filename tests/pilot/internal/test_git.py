"""Tests for GitRepo against real, throwaway git repositories."""

from __future__ import annotations

import subprocess
from pathlib import Path

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
