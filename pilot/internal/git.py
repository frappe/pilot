from __future__ import annotations

import contextlib
import os
import subprocess
import time
from pathlib import Path

from pilot.exceptions import BenchError

_STALE_TEMP_FILE_SECONDS = 24 * 60 * 60
# ext:: hands git an arbitrary command to run as the transport, so no bench input can
# ever be allowed to reach it. See gitprotocol-ext(5).
_BASE_GIT_CONFIG = {"protocol.ext.allow": "never"}


def git_env(config: dict[str, str] | None = None, base: dict | None = None) -> dict:
    """Environment that carries git config for one invocation. Credentials belong here,
    never in argv (readable in /proc) or in .git/config (outlives the call)."""
    settings = {**_BASE_GIT_CONFIG, **(config or {})}
    env = dict(os.environ if base is None else base)
    env["GIT_CONFIG_COUNT"] = str(len(settings))
    for index, (key, value) in enumerate(settings.items()):
        env[f"GIT_CONFIG_KEY_{index}"] = key
        env[f"GIT_CONFIG_VALUE_{index}"] = value
    return env


class GitRepo:
    """Git CLI wrapper with quiet read accessors."""

    def __init__(self, path: Path, config: dict[str, str] | None = None) -> None:
        self.path = Path(path)
        self.config = config or {}

    @property
    def is_cloned(self) -> bool:
        return (self.path / ".git").exists()

    @property
    def branch(self) -> str:
        """Current branch name, or '' when detached (e.g. a tag/commit checkout)."""
        return self._text("branch", "--show-current")

    @property
    def head_sha(self) -> str:
        return self._text("rev-parse", "HEAD")

    @property
    def short_head(self) -> str:
        return self._text("rev-parse", "--short", "HEAD")

    @property
    def remote_url(self) -> str:
        return self._text("remote", "get-url", "origin")

    def commit_subject(self, ref: str = "HEAD") -> str:
        return self._text("log", "-1", "--format=%s", ref)

    @property
    def has_local_changes(self) -> bool:
        """True if the tree has uncommitted edits or commits not yet on upstream."""
        if self._text("status", "--porcelain"):
            return True
        return self._text("rev-list", "--count", "@{u}..HEAD") not in ("", "0")

    def count(self, range_: str) -> int:
        """Number of commits in a range (e.g. 'HEAD..origin/main'); 0 on failure."""
        try:
            return int(self._text("rev-list", "--count", range_))
        except ValueError:
            return 0

    def has_commit(self, sha: str) -> bool:
        """Whether the object is already in this clone (no network)."""
        return bool(sha) and self._run("cat-file", "-e", f"{sha}^{{commit}}").returncode == 0

    def is_ancestor(self, ancestor: str, descendant: str) -> bool:
        """Whether `ancestor` is reachable from `descendant`. False when either
        commit is missing locally, so callers must fetch before trusting it."""
        return self._run("merge-base", "--is-ancestor", ancestor, descendant).returncode == 0

    def tracking_sha(self, branch: str) -> str:
        """SHA of the locally-cached remote branch tip (no network)."""
        if not branch:
            return ""
        return self._text("rev-parse", "--verify", "-q", f"refs/remotes/origin/{branch}")

    def remote_branch_sha(self, branch: str, timeout: float = 15) -> str:
        """SHA of origin's branch tip, queried live over the network."""
        if not branch:
            return ""
        result = self._run("ls-remote", "origin", f"refs/heads/{branch}", timeout=timeout)
        for line in result.stdout.splitlines():
            sha = line.split("\t", 1)[0].strip()
            if sha:
                return sha
        return ""

    def fetch(self, *refspecs: str, timeout: float | None = None) -> bool:
        """Best-effort fetch from origin; returns False instead of raising on failure."""
        self.prune_stale_temp_packs()
        return self._run("fetch", "origin", *refspecs, "--quiet", timeout=timeout).returncode == 0

    def prune_stale_temp_packs(self) -> None:
        """Drop pack files left behind by a fetch that was killed mid-transfer.

        A killed process runs no cleanup of its own, so this happens on the way
        into the next fetch rather than on the way out of the last one. The
        one-day floor is git's own staleness rule for these files, which keeps
        it clear of a fetch running right now.
        """
        cutoff = time.time() - _STALE_TEMP_FILE_SECONDS
        for temp_file in self.path.glob(".git/objects/pack/tmp_*"):
            with contextlib.suppress(OSError):
                if temp_file.stat().st_mtime < cutoff:
                    temp_file.unlink()

    def abort_merge_rebase(self) -> None:
        """Best-effort cleanup of an in-progress merge/rebase, e.g. before switching branches."""
        self._run("merge", "--abort")
        self._run("rebase", "--abort")

    def stash_all(self) -> bool:
        """Stash tracked and untracked changes; returns whether anything was stashed."""
        result = self._run("stash", "--include-untracked")
        return "No local changes" not in result.stdout

    def stash_pop(self) -> None:
        self._run("stash", "pop")

    def checkout_new_branch(self, branch: str, start_point: str) -> bool:
        """Create (or reset) `branch` to start at `start_point` and check it out."""
        return self._run("checkout", "-B", branch, start_point).returncode == 0

    @property
    def changed_files(self) -> list[str]:
        """Uncommitted and untracked files, as `git status --short` lines."""
        return self._checked("status", "--porcelain").splitlines()

    def has_branch(self, branch: str) -> bool:
        return self._run("show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0

    def add_worktree(self, path: Path, branch: str, start_point: str = "HEAD") -> None:
        """Check out `branch` at `path`, creating it from `start_point` if it does not exist."""
        if self.has_branch(branch):
            self._checked("worktree", "add", str(path), branch)
        else:
            self._checked("worktree", "add", "-b", branch, str(path), start_point)

    def remove_worktree(self, path: Path, force: bool = False) -> None:
        self._checked("worktree", "remove", *(["--force"] if force else []), str(path))

    def delete_branch(self, branch: str) -> None:
        """Delete a local branch. git refuses one that is not merged."""
        self._checked("branch", "-d", branch)

    def prune_worktrees(self) -> None:
        self._checked("worktree", "prune")

    def set_remote_url(self, url: str) -> bool:
        """Point origin at *url*; returns False instead of raising on failure."""
        return self._run("remote", "set-url", "origin", url).returncode == 0

    @property
    def tag_at_head(self) -> str:
        """Tag pointing exactly at HEAD, or '' when HEAD isn't on a tag."""
        return self._text("describe", "--tags", "--exact-match")

    @property
    def is_shallow(self) -> bool:
        return self._text("rev-parse", "--is-shallow-repository") == "true"

    @property
    def last_fetched(self) -> float | None:
        """mtime of the last fetch, or None if the repo was never fetched."""
        fetch_head = self.path / ".git" / "FETCH_HEAD"
        return fetch_head.stat().st_mtime if fetch_head.exists() else None

    def _checked(self, *args: str) -> str:
        """Stdout of a git command that must succeed."""
        result = self._run(*args)
        if result.returncode != 0:
            detail = (result.stderr or result.stdout).strip()
            raise BenchError(f"git {' '.join(args)} failed in {self.path}: {detail}")
        return result.stdout

    def _text(self, *args: str, timeout: float | None = None) -> str:
        result = self._run(*args, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else ""

    def _run(self, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["git", "-C", str(self.path), *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=git_env(self.config),
            )
        except (OSError, subprocess.SubprocessError):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="")
