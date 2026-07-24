from __future__ import annotations

import re
import subprocess
from functools import cached_property
from pathlib import Path

_HUNK_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")
_SHA_RE = re.compile(r"^[0-9a-fA-F]{4,40}$")
_FIELD = "\x1f"
_LOG_FORMAT = _FIELD.join(["%H", "%h", "%an", "%at", "%s", "%D"])
_INFO_FORMAT = _FIELD.join(["%H", "%h", "%an", "%ae", "%at", "%s", "%b"])


class EditorGit:
    """Editor-owned git porcelain scoped to one workspace directory.

    Isolated from pilot.internal.git.GitRepo on purpose: the editor needs stdin
    patches, combined stderr and NUL parsing that the lifecycle wrapper omits.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    @cached_property
    def is_repo(self) -> bool:
        """Cached per request: EditorGit is rebuilt per request, so repo-ness is stable here."""
        return self._text("rev-parse", "--is-inside-work-tree") == "true"

    def is_sha(self, sha: str) -> bool:
        return bool(_SHA_RE.match(sha or ""))

    def is_tracked(self, rel: str) -> bool:
        return self._run("ls-files", "--error-unmatch", "--", rel).returncode == 0

    def ignored_set(self, paths: list[str]) -> set[str]:
        if not paths:
            return set()
        result = self._run(
            "check-ignore", "--stdin", "-z", input="\x00".join(paths) + "\x00"
        )
        return {p for p in result.stdout.split("\x00") if p}

    def list_files(self, cap: int) -> list[str] | None:
        result = self._run("ls-files", "--cached", "--others", "--exclude-standard", "-z")
        if result.returncode != 0:
            return None
        return [p for p in result.stdout.split("\x00") if p][:cap]

    def status(self) -> dict:
        if not self.is_repo:
            return {"repo": False}
        branch = self._text("rev-parse", "--abbrev-ref", "HEAD") or "none"
        prefix = self._text("rev-parse", "--show-prefix")
        ahead, behind, has_upstream = 0, 0, False
        if self._run("rev-parse", "--abbrev-ref", "@{upstream}").returncode == 0:
            has_upstream = True
            counts = self._text("rev-list", "--left-right", "--count", "HEAD...@{upstream}")
            parts = counts.split()
            if len(parts) == 2:
                ahead, behind = int(parts[0]), int(parts[1])
        staged, unstaged = self._parse_status(prefix)
        return {
            "repo": True,
            "branch": branch,
            "ahead": ahead,
            "behind": behind,
            "hasUpstream": has_upstream,
            "staged": staged,
            "unstaged": unstaged,
        }

    def _parse_status(self, prefix: str) -> tuple[list[dict], list[dict]]:
        staged, unstaged = [], []
        # Raw stdout, not _text(): porcelain's leading status column is significant.
        for line in self._run("status", "--porcelain").stdout.split("\n"):
            entry = _status_entry(line, prefix)
            if entry is None:
                continue
            x, y, path = entry
            if x == "?" and y == "?":
                unstaged.append({"path": path, "code": "?"})
                continue
            if x != " ":
                staged.append({"path": path, "code": x})
            if y != " ":
                unstaged.append({"path": path, "code": y})
        return staged, unstaged

    def blame(self, rel: str) -> list[dict]:
        result = self._run("blame", "--line-porcelain", "--", rel)
        if result.returncode != 0:
            return []
        lines, cur = [], {}
        for line in result.stdout.split("\n"):
            if _is_blame_header(line):
                cur = _new_blame(line)
            elif line.startswith("\t"):
                _finalize_blame(cur)
                lines.append(cur)
            else:
                _blame_attr(cur, line)
        return lines

    def diff_gutters(self, rel: str) -> dict:
        added: list[int] = []
        modified: list[int] = []
        deleted: list[int] = []
        if self.is_repo:
            out = self._text("diff", "HEAD", "--no-color", "-U0", "--", rel)
            for line in out.split("\n"):
                match = _HUNK_RE.match(line)
                if not match:
                    continue
                old_count = _int_or(match.group(2), 1)
                new_start = _int_or(match.group(3), 0)
                new_count = _int_or(match.group(4), 1)
                if new_count == 0:
                    deleted.append(new_start)
                elif old_count == 0:
                    added.extend(range(new_start, new_start + new_count))
                else:
                    modified.extend(range(new_start, new_start + new_count))
                    if old_count > new_count:
                        deleted.append(new_start + new_count - 1)
        return {"added": added, "modified": modified, "deleted": deleted}

    def show_head(self, rel: str) -> str:
        if not self.is_repo:
            return ""
        return self._text("show", f"HEAD:./{rel}")

    def branches(self) -> dict:
        if not self.is_repo:
            return {"current": "", "branches": []}
        current = self._text("rev-parse", "--abbrev-ref", "HEAD")
        listing = self._text(
            "for-each-ref", "--format=%(refname:short)", "--sort=-committerdate", "refs/heads"
        )
        branches = [ln.strip() for ln in listing.split("\n") if ln.strip()]
        return {"current": current, "branches": branches}

    def checkout(self, branch: str, create: bool) -> tuple[bool, str]:
        args = ["checkout", "-b", branch] if create else ["checkout", branch]
        return self._combined(*args)

    def commit(self, message: str, stage_all: bool) -> tuple[bool, str]:
        if stage_all:
            ok, out = self._combined("add", "-A")
            if not ok:
                return False, out
        return self._combined("commit", "-m", message)

    def stage(self, rel: str) -> tuple[bool, str]:
        return self._combined("add", "--", rel)

    def unstage(self, rel: str) -> tuple[bool, str]:
        return self._combined("restore", "--staged", "--", rel)

    def discard(self, workspace, rel: str) -> tuple[bool, str]:
        if not self.is_tracked(rel):
            workspace.safe(rel).unlink()
            return True, ""
        return self._combined("restore", "--", rel)

    def push(self, force: bool) -> tuple[bool, str]:
        base = ["push"] + (["--force-with-lease"] if force else [])
        ok, out = self._combined(*base)
        if ok:
            return True, out
        if "no upstream branch" in out or "has no upstream" in out:
            branch = self._text("rev-parse", "--abbrev-ref", "HEAD")
            if branch and branch != "HEAD":
                ok, out = self._combined(*base, "-u", "origin", branch)
                if ok:
                    return True, out
        if "non-fast-forward" in out or "rejected" in out:
            out = "Push rejected. Pull first or force push if you're sure."
        return False, out

    def pull(self) -> tuple[bool, str]:
        ok, out = self._combined("pull", "--no-rebase")
        if not ok and "CONFLICT" in out:
            out = "Pull created merge conflicts. Resolve them in the editor, then commit."
        return ok, out

    def log(self, skip: int, limit: int) -> dict:
        if not self.is_repo:
            return {"repo": False, "commits": []}
        result = self._run(
            "log", "--no-color", f"--skip={skip}", f"-n{limit}", f"--pretty=format:{_LOG_FORMAT}"
        )
        if result.returncode != 0:
            return {"repo": True, "commits": []}
        commits = []
        for line in result.stdout.split("\n"):
            fields = line.split(_FIELD)
            if len(fields) < 6:
                continue
            commits.append(
                {
                    "sha": fields[0],
                    "short": fields[1],
                    "author": fields[2],
                    "time": int(fields[3] or 0),
                    "subject": fields[4],
                    "refs": fields[5].strip(),
                }
            )
        return {"repo": True, "commits": commits, "more": len(commits) == limit}

    def commit_info(self, sha: str) -> dict | None:
        result = self._run("show", "-s", "--no-color", f"--pretty=format:{_INFO_FORMAT}", sha)
        if result.returncode != 0:
            return None
        fields = result.stdout.rstrip("\n").split(_FIELD, 6)
        if len(fields) < 7:
            return None
        prefix = self._text("rev-parse", "--show-prefix")
        return {
            "sha": fields[0],
            "short": fields[1],
            "author": fields[2],
            "email": fields[3],
            "time": int(fields[4] or 0),
            "subject": fields[5],
            "body": fields[6].rstrip("\n"),
            "files": self._commit_files(sha, prefix),
        }

    def _commit_files(self, sha: str, prefix: str) -> list[dict]:
        result = self._run(
            "diff-tree", "--no-commit-id", "--name-status", "-r", "--root", "-z", sha
        )
        parts = [p for p in result.stdout.split("\x00")]
        files, i = [], 0
        while i < len(parts):
            code = parts[i]
            i += 1
            if not code or i >= len(parts):
                continue
            path = parts[i]
            i += 1
            if code[0] in ("R", "C") and i < len(parts):
                path = parts[i]
                i += 1
            if prefix:
                if not path.startswith(prefix):
                    continue
                path = path[len(prefix):]
            files.append({"path": path, "code": code[0]})
        return files

    def commit_file(self, sha: str, rel: str) -> dict:
        """File contents before and after a commit, for a two-sided diff view."""
        if not self.is_repo:
            return {"old": "", "new": ""}
        return {
            "old": self._text("show", f"{sha}^:./{rel}"),
            "new": self._text("show", f"{sha}:./{rel}"),
        }

    def _text(self, *args: str) -> str:
        result = self._run(*args)
        return result.stdout.strip() if result.returncode == 0 else ""

    def _combined(self, *args: str, input: str | None = None) -> tuple[bool, str]:
        result = self._run(*args, input=input, combine=True)
        return result.returncode == 0, result.stdout.strip()

    def _run(
        self, *args: str, input: str | None = None, combine: bool = False
    ) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                ["git", *args],
                cwd=self.root,
                text=True,
                input=input,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT if combine else subprocess.PIPE,
            )
        except (OSError, subprocess.SubprocessError):
            return subprocess.CompletedProcess(args, 1, "", "")


def _status_entry(line: str, prefix: str) -> tuple[str, str, str] | None:
    if len(line) < 4:
        return None
    x, y, path = line[0], line[1], line[3:].strip('"')
    if " -> " in path:
        path = path.split(" -> ", 1)[1].strip('"')
    if prefix:
        if not path.startswith(prefix):
            return None
        path = path[len(prefix):]
    return x, y, path


def _is_blame_header(line: str) -> bool:
    head = line[:40]
    return len(line) >= 40 and _is_hex(head) and (len(line) == 40 or line[40] == " ")


def _new_blame(line: str) -> dict:
    cur = {"sha": line[:40], "author": "", "time": 0, "summary": ""}
    fields = line.split()
    if len(fields) >= 3:
        cur["line"] = int(fields[2])
    return cur


def _blame_attr(cur: dict, line: str) -> None:
    if line.startswith("author "):
        cur["author"] = line[7:]
    elif line.startswith("author-time "):
        cur["time"] = int(line[12:])
    elif line.startswith("summary "):
        cur["summary"] = line[8:]


def _finalize_blame(cur: dict) -> None:
    if cur.get("sha", "").startswith("0000000"):
        cur["author"] = "Uncommitted"


def _is_hex(text: str) -> bool:
    return all(c in "0123456789abcdef" for c in text)


def _int_or(value: str | None, default: int) -> int:
    return int(value) if value else default
