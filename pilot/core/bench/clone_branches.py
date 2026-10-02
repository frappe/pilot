from pathlib import Path

from pilot.exceptions import BenchError
from pilot.internal.git import GitRepo, git_env
from pilot.utils import run_command


def parse_app_branches(value: str) -> dict[str, str]:
    branches = {}
    for entry in value.split(",") if value.strip() else []:
        app, separator, branch = entry.strip().partition("=")
        if not separator or not app.strip() or not branch.strip():
            raise BenchError("Use comma-separated app=branch overrides, such as frappe=develop.")
        branches[app.strip()] = branch.strip()
    return branches


def validate_branches(branch: str, app_branches: dict[str, str]) -> None:
    if not isinstance(branch, str) or not branch.strip():
        raise BenchError("Select default, current, or a branch name.")
    if not isinstance(app_branches, dict) or not all(
        isinstance(app, str) and app and isinstance(value, str) and value
        for app, value in app_branches.items()
    ):
        raise BenchError("App branch overrides must map app names to branch names.")
    for value in [branch, *app_branches.values()]:
        if value not in ("default", "current"):
            run_command(["git", "check-ref-format", "--branch", value], env=git_env())


def clone_branch(source: Path, destination: Path, branch: str) -> str:
    """Create a clean checkout, fetching the selected branch only in the destination."""
    remote = GitRepo(source).remote_url
    if not remote:
        raise BenchError(f"{source.name} has no origin remote; use --branch current to copy its checkout.")
    if branch == "default":
        output = run_command(
            ["git", "-C", str(source), "ls-remote", "--symref", "origin", "HEAD"], env=git_env()
        ).stdout.decode()
        branch = next(
            (
                line.split("refs/heads/", 1)[1].split()[0]
                for line in output.splitlines()
                if line.startswith("ref: refs/heads/")
            ),
            "",
        )
        if not branch:
            raise BenchError(f"Could not determine the default branch of {source.name}'s origin.")
    run_command(
        ["git", "clone", "--no-hardlinks", "--no-checkout", str(source), str(destination)],
        env=git_env(),
    )
    run_command(["git", "-C", str(destination), "remote", "set-url", "origin", remote], env=git_env())
    run_command(
        [
            "git",
            "-C",
            str(destination),
            "fetch",
            "origin",
            f"+refs/heads/{branch}:refs/remotes/origin/{branch}",
        ],
        env=git_env(),
    )
    run_command(["git", "-C", str(destination), "checkout", "-B", branch, f"origin/{branch}"], env=git_env())
    return branch
