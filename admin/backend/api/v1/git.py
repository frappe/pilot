from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from admin.backend.api.responses import error_response, no_content_response
from pilot.core.bench import Bench
from pilot.integrations.central.client import CentralClient, CentralClientError
from pilot.integrations.git import (
    TOKEN_HELP_URLS,
    BranchNotFoundError,
    GitAuthError,
    GitCredentialStore,
    GitHubAppGrant,
    GitProviderError,
    parse_github_owner_repo,
    provider_for_name,
    provider_for_repo,
    resolve_app_name_from_repo,
    token_for,
)

git_bp = Blueprint("git", __name__)


def _store() -> GitCredentialStore:
    return GitCredentialStore(Path(current_app.config["BENCH_ROOT"]))


def _current_bench() -> Bench:
    return Bench(Path(current_app.config["BENCH_ROOT"]))


def _mask_token(token: str) -> str:
    if len(token) <= 8:
        return token
    return f"{token[:4]}{'x' * 8}{token[-4:]}"


def _central_github(store: GitCredentialStore) -> dict | None:
    if not _current_bench().config.central.enabled:
        return None
    try:
        return GitHubAppGrant(store).get_status()
    except CentralClientError as exc:
        return {"available": False, "connections": [], "error": str(exc)}


def _status(record: dict | None, central: dict | None = None) -> dict:
    status = {"connected": False, "providers": TOKEN_HELP_URLS}
    if central is not None:
        status["central"] = central
    if not record:
        return status
    return {
        **status,
        "connected": True,
        "auth_type": record.get("auth_type", "token"),
        "provider": record.get("provider"),
        "username": record.get("username", ""),
        "token_preview": _mask_token(record.get("token", "")),
        "is_token_valid": record.get("is_token_valid", True),
        "token_expires_at": record.get("token_expires_at"),
    }


@git_bp.get("/connection")
def get_integration():
    store = _store()
    central = _central_github(store)
    return jsonify(_status(store.load(), central))


@git_bp.post("/connection-attempts")
def start_central_connection():
    data = request.get_json(silent=True)
    return_url = data.get("return_url") if isinstance(data, dict) else None
    if not isinstance(return_url, str) or not return_url.startswith(("http://", "https://")):
        return error_response("invalid_return_url", "return_url must be an http or https URL.", 422)
    if not _current_bench().config.central.enabled:
        return error_response("central_unavailable", "This server is not managed by Frappe Cloud.", 422)
    try:
        url = CentralClient().start_github_connection(return_url)
    except CentralClientError as exc:
        return error_response("central_unavailable", f"Could not reach Frappe Cloud: {exc}", 502)
    return jsonify({"url": url})


@git_bp.put("/connection")
def save_integration():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    if any(
        value is not None and not isinstance(value, str)
        for value in (
            data.get("provider"),
            data.get("token"),
            data.get("username"),
            data.get("expires_at"),
        )
    ):
        return error_response("invalid_git_integration", "Git integration fields must be strings.", 422)

    if GitHubAppGrant(_store()).is_active:
        return error_response(
            "github_app_connected",
            "Disconnect the Frappe Cloud GitHub connection before adding a token.",
            409,
        )

    provider_name = (data.get("provider") or "github").strip().lower()
    token = (data.get("token") or "").strip()
    username = (data.get("username") or "").strip()
    expires_at = (data.get("expires_at") or "").strip() or None
    if provider_name not in TOKEN_HELP_URLS:
        return error_response("invalid_git_provider", "Choose a supported git provider.", 422)
    if not token:
        return error_response("token_required", "A personal access token is required.", 422)
    try:
        provider = provider_for_name(provider_name, token)
        account = provider.validate()
    except GitAuthError:
        return error_response(
            "invalid_git_token",
            "That token was rejected. Check its scopes and expiration.",
            401,
            {"token_invalid": True},
        )
    except GitProviderError:
        return error_response("git_provider_unavailable", "Could not connect to the git provider.", 500)
    record = _store().save(
        provider_name, token, username=username or account.get("login", ""), expires_at=expires_at
    )
    _current_bench().audit_action(
        "git", {"event": "connected", "provider": provider_name, "username": record.get("username", "")}
    )
    return jsonify(_status(record))


@git_bp.delete("/connection")
def delete_integration():
    store = _store()
    grant = GitHubAppGrant(store)
    if grant.is_active:
        try:
            grant.disconnect()
        except CentralClientError as exc:
            return error_response("central_unavailable", f"Could not reach Frappe Cloud: {exc}", 502)
    store.clear()
    _current_bench().audit_action("git", {"event": "disconnected"})
    return no_content_response()


@git_bp.get("/repositories")
def list_repos():
    store = _store()
    if GitHubAppGrant(store).is_active:
        try:
            return jsonify(CentralClient().get_github_repositories())
        except CentralClientError as exc:
            return error_response("central_unavailable", f"Could not reach Frappe Cloud: {exc}", 502)

    record = store.load()
    if not record or not record.get("token"):
        return error_response("git_auth_required", "No git provider is connected.", 401)
    try:
        provider = provider_for_name(record["provider"], record["token"])
        repos = provider.list_repos()
    except GitAuthError:
        store.mark_invalid()
        return error_response(
            "invalid_git_token",
            "Your access token has expired or been revoked.",
            401,
            {"token_invalid": True},
        )
    except GitProviderError:
        return error_response("git_provider_unavailable", "Could not list repositories.", 500)
    store.mark_valid()
    return jsonify(repos)


@git_bp.get("/branches")
def list_branches():
    repo_url = request.args.get("repo", "").strip()
    if not repo_url:
        return error_response("repository_required", "repo parameter is required.", 422)

    store = _store()
    record = store.load()
    token = token_for(store.path.parent, repo_url) if record and record.get("is_token_valid", True) else ""

    try:
        owner, name = parse_github_owner_repo(repo_url)
    except GitProviderError:
        return error_response("invalid_repository", "Enter a valid repository URL.", 422)

    full_name = f"{owner}/{name}"
    provider = provider_for_repo(repo_url, token) or provider_for_name("github", token)
    try:
        branches = provider.list_branches(full_name)
    except GitAuthError:
        # A rejected repository token says nothing about the GitHub App grant itself.
        if not GitHubAppGrant(store).is_active:
            store.mark_invalid()
        return error_response(
            "invalid_git_token",
            "Your access token has expired or been revoked.",
            401,
            {"token_invalid": True},
        )
    except GitProviderError:
        return error_response("git_provider_unavailable", "Could not list repository branches.", 500)

    try:
        default_branch = provider.get_default_branch(full_name)
    except GitProviderError:
        default_branch = ""
    if default_branch in branches:
        branches = [default_branch, *(b for b in branches if b != default_branch)]

    return jsonify({"branches": branches, "default_branch": default_branch})


def _resolution_request(data) -> tuple[str, str] | tuple[None, None]:
    """(repo, branch) from the payload, or (None, None) when it is unusable."""
    if not isinstance(data, dict):
        return None, None
    if any(
        value is not None and not isinstance(value, str) for value in (data.get("repo"), data.get("branch"))
    ):
        return None, None
    return (data.get("repo") or "").strip(), (data.get("branch") or "").strip()


@git_bp.post("/repository-resolutions")
def resolve_app():
    bench_root = Path(current_app.config["BENCH_ROOT"])
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return error_response("malformed_request", "Expected a JSON object.", 400)
    repo_url, branch = _resolution_request(data)
    if repo_url is None:
        return error_response("invalid_repository", "Repository fields must be strings.", 422)
    if not repo_url:
        return error_response("repository_required", "repo is required.", 422)
    if provider_for_repo(repo_url) is None:
        return error_response("invalid_repository", "Enter a supported repository URL.", 422)
    return _resolved_app_response(bench_root, repo_url, branch)


def _resolved_app_response(bench_root: Path, repo_url: str, branch: str):
    try:
        resolved = resolve_app_name_from_repo(bench_root, repo_url, branch)
    except BranchNotFoundError as exc:
        return error_response("branch_not_found", str(exc), 422)
    except GitAuthError:
        return error_response(
            "invalid_git_token",
            "Your access token has expired or been revoked.",
            401,
            {"token_invalid": True},
        )
    except GitProviderError:
        return error_response(
            "invalid_app_repository",
            "Could not resolve a Frappe app from this repository.",
            422,
        )
    except Exception:
        return error_response("repository_unavailable", "Could not resolve the app repository.", 500)
    return jsonify(resolved)
