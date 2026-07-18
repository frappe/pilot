from __future__ import annotations

from typing import TYPE_CHECKING

from pilot.core.app.revisions import RevisionPin
from pilot.exceptions import BenchError, CommandError
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.internal.git import GitRepo


class AppRepository:
    def __init__(self, app: "App") -> None:
        self.app = app

    @property
    def repo(self) -> "GitRepo":
        from pilot.internal.git import GitRepo

        return GitRepo(self.app.path)

    @property
    def installed_hash(self) -> str:
        """Full SHA of the app's current HEAD, or '' if it can't be resolved."""
        return self.repo.head_sha

    @property
    def installed_tag(self) -> str:
        """Tag checked out exactly at HEAD, or '' if HEAD isn't on a tag."""
        return self.repo.tag_at_head

    def is_on_revision(self, pin: RevisionPin) -> bool:
        """Whether this app is currently checked out at a pinned revision."""
        if pin.kind == "tag":
            return self.installed_tag == pin.ref

        hash = self.installed_hash
        return bool(hash) and hash.startswith(pin.ref)

    def has_marketplace_update(self, marketplace_entry: dict | None) -> bool:
        """Whether a newer version is available, per this app's marketplace entry."""
        target = self._matching_marketplace_target(marketplace_entry)
        pin = RevisionPin.from_marketplace_target(target) if target else None
        if pin is not None:
            return not self.is_on_revision(pin)
        return self.app.has_remote_update()

    def _matching_marketplace_target(self, marketplace_entry: dict | None) -> dict | None:
        if not marketplace_entry or self.app.config.repo != marketplace_entry["repo"]:
            return None
        version = self.app.installed_version
        return next((t for t in marketplace_entry["targets"] if t["version"] == version), None)

    def has_remote_update(self) -> bool:
        """Check the remote branch tip without downloading objects."""
        if not self.app.config.branch:
            return False
        remote_sha = self.repo.remote_branch_sha(self.app.config.branch)
        return bool(remote_sha and self.installed_hash and remote_sha != self.installed_hash)

    @property
    def remote_url(self) -> str:
        """The clone URL to use, token-embedded when the repo is private."""
        from pilot.integrations.git import authenticated_url_for

        return authenticated_url_for(self.app.bench.path, self.app.config.repo)

    def detect_default_branch(self) -> str:
        import subprocess

        remote = self.remote_url
        result = subprocess.run(
            ["git", "ls-remote", "--symref", remote, "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        for line in result.stdout.splitlines():
            if line.startswith("ref: refs/heads/"):
                return line.split("refs/heads/")[1].split()[0]
        refs = subprocess.run(
            ["git", "ls-remote", "--heads", remote],
            capture_output=True,
            text=True,
            timeout=10,
        ).stdout
        for candidate in ("develop", "master", "version-16", "version-15"):
            if f"refs/heads/{candidate}" in refs:
                return candidate
        return "develop"

    @staticmethod
    def is_commit_hash(ref: str) -> bool:
        import re

        return bool(re.fullmatch(r"[0-9a-f]{7,40}", ref))

    def clone_rev(self, commit: str) -> None:
        run_command(["git", "clone", self.remote_url, str(self.app.path)], stream_output=True)
        try:
            run_command(["git", "-C", str(self.app.path), "checkout", commit])
        except CommandError:
            raise BenchError(f"Commit '{commit}' not found in {self.app.config.repo}.")

    def clone(self) -> None:
        target = self.app.config.branch or self.detect_default_branch()
        if self.is_commit_hash(target):
            self.clone_rev(target)
        else:
            run_command(
                [
                    "git",
                    "clone",
                    self.remote_url,
                    "--branch",
                    target,
                    "--depth",
                    "1",
                    str(self.app.path),
                ],
                stream_output=True,
            )

    @property
    def is_shallow(self) -> bool:
        return self.repo.is_shallow

    @staticmethod
    def pack_threads() -> int:
        import os

        cpus = os.cpu_count() or 1
        # Keep git from saturating small servers.
        if cpus <= 2:
            return 1
        return max(1, cpus // 2)

    def update(self, pin: RevisionPin | None = None) -> None:
        """Pull the latest code or move to a pinned revision."""
        if pin is not None:
            self.checkout_pinned_target(pin)
            return

        cmd = [
            "git",
            "-c",
            f"pack.threads={self.pack_threads()}",
            "-C",
            str(self.app.path),
            "fetch",
            "origin",
            self.app.config.branch,
        ]
        if self.is_shallow:
            cmd.append("--depth=1")
        run_command(cmd)
        run_command(
            [
                "git",
                "-C",
                str(self.app.path),
                "reset",
                "--hard",
                f"origin/{self.app.config.branch}",
            ]
        )

    def switch_branch(self, branch: str) -> None:
        if not self.app.is_cloned:
            raise BenchError(f"'{self.app.config.name}' is not cloned at {self.app.path}")

        repo = self.repo
        repo.fetch("+refs/heads/*:refs/remotes/origin/*")
        repo.abort_merge_rebase()
        stashed = repo.stash_all()
        if not repo.checkout_new_branch(branch, f"origin/{branch}"):
            if stashed:
                repo.stash_pop()
            raise BenchError(f"Could not switch '{self.app.config.name}' to branch '{branch}'.")
        self.app.config.branch = branch

    def checkout_pinned_target(self, pin: RevisionPin) -> None:
        if pin.kind == "tag":
            run_command(
                ["git", "-C", str(self.app.path), "fetch", "--depth", "1", "origin", pin.ref]
            )
            run_command(["git", "-C", str(self.app.path), "checkout", "FETCH_HEAD"])
        else:
            self.checkout_pinned_commit(pin.ref)

    def checkout_pinned_commit(self, sha: str) -> None:
        """Check out a specific commit SHA."""
        try:
            run_command(["git", "-C", str(self.app.path), "fetch", "--depth", "1", "origin", sha])
            run_command(["git", "-C", str(self.app.path), "checkout", "FETCH_HEAD"])
            return
        except CommandError:
            pass
        unshallow_flag = ["--unshallow"] if self.is_shallow else []
        run_command(
            [
                "git",
                "-C",
                str(self.app.path),
                "fetch",
                *unshallow_flag,
                "origin",
                self.app.config.branch,
            ]
        )
        run_command(["git", "-C", str(self.app.path), "checkout", sha])
