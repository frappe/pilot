from __future__ import annotations

import subprocess
from pathlib import Path


class GitRepo:
    """A robust wrapper over the ``git`` CLI for one working tree.

    Read accessors degrade to an empty/default value on any failure, so callers
    stay free of try/except noise. Only network/mutating helpers surface failure.
    """

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

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

    def has_remote_update(self) -> bool:
        """Whether origin's branch tip is ahead of local HEAD (one network call)."""
        remote = self.remote_branch_sha(self.branch)
        local = self.head_sha
        return bool(remote and local and remote != local)

    def fetch(self, *refspecs: str, timeout: float | None = None) -> bool:
        """Best-effort fetch from origin; returns False instead of raising on failure."""
        return self._run("fetch", "origin", *refspecs, "--quiet", timeout=timeout).returncode == 0

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

    def _text(self, *args: str, timeout: float | None = None) -> str:
        result = self._run(*args, timeout=timeout)
        return result.stdout.strip() if result.returncode == 0 else ""

    def _run(self, *args: str, timeout: float | None = None) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["git", "-C", str(self.path), *args],
                capture_output=True, text=True, timeout=timeout,
            )
        except (OSError, subprocess.SubprocessError):
            return subprocess.CompletedProcess(args, returncode=1, stdout="", stderr="")
