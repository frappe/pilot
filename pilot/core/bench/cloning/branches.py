from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pilot.core.bench.cloning.repository import clone_repository
from pilot.exceptions import BenchError, CommandError
from pilot.integrations.git import auth_config_for
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
    validate_app_branches(app_branches)
    for value in [branch, *app_branches.values()]:
        if value not in ("default", "current"):
            run_command(["git", "check-ref-format", "--branch", value], env=git_env())


def validate_app_branches(app_branches: dict[str, str]) -> None:
    if not isinstance(app_branches, dict) or not all(
        isinstance(app, str) and app and isinstance(value, str) and value
        for app, value in app_branches.items()
    ):
        raise BenchError("App branch overrides must map app names to branch names.")


def get_remote_branches(source: Path, git_config: dict[str, str] | None = None) -> dict:
    if not GitRepo(source).remote_url:
        raise BenchError(f"{source.name} has no origin remote.")
    try:
        output = run_command(
            ["git", "-C", str(source), "ls-remote", "--symref", "origin", "HEAD", "refs/heads/*"],
            env=git_env(git_config),
            timeout=15,
        ).stdout.decode()
    except CommandError as error:
        raise BenchError(f"Could not read branches for {source.name}. Check access to origin.") from error
    default = ""
    branches = set()
    for line in output.splitlines():
        if line.startswith("ref: refs/heads/"):
            default = line.split("refs/heads/", 1)[1].split()[0]
        elif "\trefs/heads/" in line:
            branches.add(line.split("\trefs/heads/", 1)[1])
    if not default or default not in branches:
        raise BenchError(f"Could not determine the default branch of {source.name}'s origin.")
    return {
        "name": source.name,
        "default_branch": default,
        "branches": [default, *sorted(branches - {default})],
    }


def get_app_branch_options(source) -> list[dict]:
    apps = source.apps()
    with ThreadPoolExecutor(max_workers=4) as executor:
        return list(
            executor.map(
                get_remote_branches,
                [app.path for app in apps],
                [auth_config_for(app.bench.path, app.config.repo) for app in apps],
            )
        )


def clone_branch(
    source: Path, destination: Path, branch: str, git_config: dict[str, str] | None = None
) -> str:
    """Create a clean checkout, fetching the selected branch only in the destination."""
    remote = GitRepo(source).remote_url
    if not remote:
        raise BenchError(f"{source.name} has no origin remote; use --branch current to copy its checkout.")
    if branch == "default":
        branch = get_remote_branches(source, git_config)["default_branch"]
    clone_repository(source, destination)
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
        env=git_env(git_config),
    )
    run_command(["git", "-C", str(destination), "checkout", "-B", branch, f"origin/{branch}"], env=git_env())
    return branch
