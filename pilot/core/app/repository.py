from __future__ import annotations

import shutil
from typing import TYPE_CHECKING

from pilot.core.app.revisions import RevisionPin
from pilot.exceptions import BenchError, CommandError
from pilot.utils import run_command

if TYPE_CHECKING:
    from pilot.core.app import App
    from pilot.internal.git import GitRepo

_FETCH_TIMEOUT_SECONDS = 30


class AppRepository:
    def __init__(self, app: "App") -> None:
        self.app = app

    @property
    def repo(self) -> "GitRepo":
        from pilot.internal.git import GitRepo

        return GitRepo(self.app.path, self.git_config)

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

    def has_marketplace_update(self) -> bool:
        """Whether a newer version is available, per this app's marketplace entry."""
        pin = self.update_target()
        return pin is not None and not self.is_on_revision(pin)

    def update_target(self) -> RevisionPin | None:
        """The fixed revision this app would update to, or None when there is none. (forward only)"""
        entry = self.marketplace_entry
        if entry:
            release = self.forward_release(entry)
            return RevisionPin.from_marketplace_release(release) if release else None
        if not self.app.config.branch:
            return None
        tip = self.repo.remote_branch_sha(self.app.config.branch)
        return RevisionPin(kind="commit", ref=tip) if tip else None

    @property
    def marketplace_entry(self) -> dict | None:
        """The registry entry describing this app's own repository, or None when
        the app is unlisted or its repo is a fork of a listed one."""
        from pilot.integrations.git.base import same_repository
        from pilot.integrations.marketplace import Marketplace

        entry = Marketplace.registry_by_name().get(self.app.config.name)
        if not entry or not same_repository(self.app.config.repo, entry.get("repo", "")):
            return None
        return entry

    def forward_release(self, marketplace_entry: dict) -> dict | None:
        """The newest release advertised for this app's branch, when git says it
        is ahead of the checked-out commit. Releases arrive newest-first."""
        from pilot.integrations.marketplace import Marketplace

        newest = next(
            (
                r
                for r in Marketplace.releases(marketplace_entry["name"])
                if r.get("branch") == self.app.config.branch
            ),
            None,
        )
        if newest is None or not newest.get("commit"):
            return None
        return newest if self._is_ahead_of_installed(newest["commit"]) else None

    def _is_ahead_of_installed(self, commit: str) -> bool:
        """Whether `commit` is a step forward from HEAD, asking git rather than
        comparing version labels."""
        installed = self.installed_hash
        if installed == commit:
            return False
        if not installed:
            return True  # HEAD is unreadable - moving to the published commit is the fix
        repo = self.repo
        if not repo.has_commit(commit):
            self._sync_remote_url()
            repo.fetch(commit, timeout=_FETCH_TIMEOUT_SECONDS)
        return not repo.is_ancestor(commit, installed)

    @property
    def remote_url(self) -> str:
        """The clone URL, always credential-free: the token rides in git_config."""
        return self.app.config.repo

    @property
    def git_config(self) -> dict[str, str]:
        """Per-invocation git config authenticating this app's remote, if a token applies."""
        from pilot.integrations.git import auth_config_for

        return auth_config_for(self.app.bench.path, self.app.config.repo)

    @property
    def git_env(self) -> dict:
        """Environment for a git call that talks to this app's remote."""
        from pilot.internal.git import git_env

        return git_env(self.git_config)

    def get_default_branch(self) -> str:
        import subprocess

        remote, environment = self.remote_url, self.git_env
        result = subprocess.run(
            ["git", "ls-remote", "--symref", remote, "HEAD"],
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
        )
        for line in result.stdout.splitlines():
            if line.startswith("ref: refs/heads/"):
                return line.split("refs/heads/")[1].split()[0]
        refs = subprocess.run(
            ["git", "ls-remote", "--heads", remote],
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
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
        # Git fetches a commit on its own only by its full SHA; a short one is found in the full history.
        if len(commit) == 40 and self.depth_flags and not self.app.path.exists():
            try:
                self.fetch_commit(commit)
                return
            except CommandError:
                shutil.rmtree(self.app.path, ignore_errors=True)

        run_command(
            ["git", "clone", self.remote_url, str(self.app.path)],
            env=self.git_env,
            stream_output=True,
        )
        try:
            run_command(["git", "-C", str(self.app.path), "checkout", commit])
        except CommandError as exc:
            shutil.rmtree(self.app.path, ignore_errors=True)
            raise BenchError(f"Commit '{commit}' not found in {self.app.config.repo}.") from exc

    def fetch_commit(self, commit: str) -> None:
        """Clone only `commit`, without its history, which a large app like erpnext cannot afford."""
        path = str(self.app.path)
        run_command(["git", "init", "--quiet", path])
        run_command(["git", "-C", path, "remote", "add", "origin", self.remote_url])
        run_command(
            ["git", "-C", path, "fetch", *self.depth_flags, "origin", commit],
            env=self.git_env,
            stream_output=True,
        )
        run_command(["git", "-C", path, "checkout", "--quiet", "FETCH_HEAD"])

    def clone(self) -> None:
        target = self.app.config.branch or self.get_default_branch()
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
                    *self.depth_flags,
                    str(self.app.path),
                ],
                env=self.git_env,
                stream_output=True,
            )
            self.app.config.branch = target

    @property
    def is_shallow(self) -> bool:
        return self.repo.is_shallow

    @property
    def depth_flags(self) -> list[str]:
        """Depth arguments for a clone or a single-ref fetch. Empty on a dev install,
        where apps are checked out with their full history to work on."""
        from pilot import is_dev_build

        return [] if is_dev_build else ["--depth", "1"]

    @staticmethod
    def pack_threads() -> int:
        import os

        cpus = os.cpu_count() or 1
        # Keep git from saturating small servers.
        if cpus <= 2:
            return 1
        return max(1, cpus // 2)

    def _sync_remote_url(self) -> None:
        """Drop credentials a clone from an older version embedded in origin. The token
        now rides in git config per call, so .git/config never has to hold one."""
        from pilot.integrations.git import without_credentials

        current = self.repo.remote_url
        clean = without_credentials(current)
        if current and clean != current:
            self.repo.set_remote_url(clean)

    def update(self, pin: RevisionPin | None = None) -> None:
        """Pull the latest code or move to a pinned revision."""
        if pin is not None:
            self.checkout_pinned_target(pin)
            return

        self._sync_remote_url()
        self.repo.prune_stale_temp_packs()
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
            cmd.append("--depth=1")  # an app cloned before a dev install stays as it is
        run_command(cmd, env=self.git_env)
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
        self._sync_remote_url()
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
            self._sync_remote_url()
            self.repo.prune_stale_temp_packs()
            run_command(
                ["git", "-C", str(self.app.path), "fetch", *self.depth_flags, "origin", pin.ref],
                env=self.git_env,
            )
            self._checkout_pinned_ref("FETCH_HEAD")
        else:
            self.checkout_pinned_commit(pin.ref)

    def checkout_pinned_commit(self, sha: str) -> None:
        """Check out a specific commit SHA, staying on the app's configured branch."""
        self._sync_remote_url()
        self.repo.prune_stale_temp_packs()
        try:
            run_command(
                ["git", "-C", str(self.app.path), "fetch", *self.depth_flags, "origin", sha],
                env=self.git_env,
            )
            self._checkout_pinned_ref("FETCH_HEAD")
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
            ],
            env=self.git_env,
        )
        self._checkout_pinned_ref(sha)

    def _checkout_pinned_ref(self, ref: str) -> None:
        """Land on `ref`, keeping the configured branch name attached instead of a detached HEAD.

        Stashes first, as switching branches does. Building an app's assets writes
        generated files back into its own repo - components.d.ts, yarn.lock - so
        the update after a build would find a dirty tree and git would refuse to
        check anything out. The stash is left alone on success: the revision being
        moved to is free to conflict with it, and dropping it would lose work.
        """
        stashed = self.repo.stash_all()
        branch = self.app.config.branch
        if branch and not self.is_commit_hash(branch) and self.repo.checkout_new_branch(branch, ref):
            return
        try:
            run_command(["git", "-C", str(self.app.path), "checkout", ref])
        except CommandError:
            if stashed:
                self.repo.stash_pop()
            raise
