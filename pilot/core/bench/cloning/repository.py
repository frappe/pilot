import os
import sys
import tempfile
from pathlib import Path

from pilot.exceptions import CommandError
from pilot.internal.git import git_env
from pilot.utils import run_command


def clone_repository(source: Path, destination: Path) -> None:
    if not destination.exists() and try_reflink_clone(source, destination):
        return
    run_command(
        ["git", "clone", "--no-hardlinks", "--dissociate", "--no-checkout", str(source), str(destination)],
        env=git_env(),
    )


def try_reflink_clone(source: Path, destination: Path) -> bool:
    if sys.platform not in {"linux", "darwin"}:
        return False
    objects = Path(
        run_command(
            ["git", "-C", str(source), "rev-parse", "--path-format=absolute", "--git-path", "objects"],
            env=git_env(),
        )
        .stdout.decode()
        .strip()
    )
    if not has_portable_objects(source, objects):
        return False
    with tempfile.TemporaryDirectory(prefix=".pilot-git-", dir=destination.parent) as temporary:
        staging = Path(temporary)
        target = staging / ".git" / "objects"
        target.mkdir(parents=True)
        flag = "--reflink=always" if sys.platform == "linux" else "-c"
        try:
            run_command(["cp", "-a", flag, str(objects) + "/.", str(target)])
            initialize_repository(source, staging)
            # Source maintenance can remove objects while its refs are read.
            run_command(
                ["git", "-C", str(staging), "fsck", "--connectivity-only", "--no-dangling"],
                env=git_env(),
            )
        except CommandError:
            return False
        staging.rename(destination)
    return True


def has_portable_objects(source: Path, objects: Path) -> bool:
    if os.environ.get("GIT_ALTERNATE_OBJECT_DIRECTORIES"):
        return False
    if objects.is_symlink() or (objects / "info" / "alternates").exists():
        return False
    shallow = run_command(
        ["git", "-C", str(source), "rev-parse", "--is-shallow-repository"], env=git_env()
    ).stdout.strip()
    if shallow != b"false":
        return False
    for directory, directories, files in os.walk(objects):
        if any(Path(directory, name).is_symlink() for name in [*directories, *files]):
            return False
        if any(name.endswith(".promisor") for name in files):
            return False
    return True


def initialize_repository(source: Path, destination: Path) -> None:
    prefix = ["git", "-C", str(source)]
    object_format = (
        run_command([*prefix, "rev-parse", "--show-object-format"], env=git_env()).stdout.decode().strip()
    )
    run_command(["git", "init", f"--object-format={object_format}", str(destination)], env=git_env())
    references = (
        run_command(
            [*prefix, "for-each-ref", "--format=%(objectname) %(refname)", "refs/heads", "refs/tags"],
            env=git_env(),
        )
        .stdout.decode()
        .splitlines()
    )
    updates = []
    for reference in references:
        revision, name = reference.split(" ", 1)
        if name.startswith("refs/heads/"):
            name = name.replace("refs/heads/", "refs/remotes/origin/", 1)
        updates.append(f"update {name} {revision}")
    revision = run_command([*prefix, "rev-parse", "HEAD"], env=git_env()).stdout.decode().strip()
    head = (
        run_command([*prefix, "rev-parse", "--symbolic-full-name", "HEAD"], env=git_env())
        .stdout.decode()
        .strip()
        .removeprefix("refs/heads/")
    )
    if head == "HEAD":
        updates.extend(["option no-deref", f"update HEAD {revision}"])
    else:
        run_command(
            ["git", "-C", str(destination), "symbolic-ref", "HEAD", f"refs/heads/{head}"], env=git_env()
        )
        updates.append(f"update refs/heads/{head} {revision}")
    run_command(
        ["git", "-C", str(destination), "update-ref", "--stdin"],
        env=git_env(),
        stdin_text="\n".join(updates) + "\n",
    )
    run_command(["git", "-C", str(destination), "remote", "add", "origin", str(source)], env=git_env())
    if head != "HEAD":
        run_command(
            ["git", "-C", str(destination), "config", f"branch.{head}.remote", "origin"], env=git_env()
        )
        run_command(
            ["git", "-C", str(destination), "config", f"branch.{head}.merge", f"refs/heads/{head}"],
            env=git_env(),
        )
        run_command(
            [
                "git",
                "-C",
                str(destination),
                "symbolic-ref",
                "refs/remotes/origin/HEAD",
                f"refs/remotes/origin/{head}",
            ],
            env=git_env(),
        )
