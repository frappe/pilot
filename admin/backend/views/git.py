from __future__ import annotations

from pathlib import Path

from flask import Blueprint, current_app, jsonify, request

from pilot.core.git_providers import (
    TOKEN_HELP_URLS,
    GitAuthError,
    GitCredentialStore,
    GitProviderError,
    provider_for_name,
)


git_bp = Blueprint("git", __name__)


def _store() -> GitCredentialStore:
    return GitCredentialStore(Path(current_app.config["BENCH_ROOT"]))


def _status(record: dict | None) -> dict:
    if not record:
        return {"connected": False, "providers": TOKEN_HELP_URLS}
    return {
        "connected": True,
        "provider": record.get("provider"),
        "is_token_valid": record.get("is_token_valid", True),
        "token_expires_at": record.get("token_expires_at"),
        "providers": TOKEN_HELP_URLS,
    }


@git_bp.route("/integration", methods=["GET"])
def get_integration():
    return jsonify(_status(_store().load()))


@git_bp.route("/integration", methods=["POST"])
def save_integration():
    data = request.get_json(silent=True) or {}
    provider_name = (data.get("provider") or "github").strip().lower()
    token = (data.get("token") or "").strip()
    expires_at = (data.get("expires_at") or "").strip() or None
    if not token:
        return jsonify({"ok": False, "error": "A personal access token is required."})
    try:
        provider = provider_for_name(provider_name, token)
        account = provider.validate()
    except GitAuthError:
        return jsonify({"ok": False, "error": "That token was rejected. Check it has the required scopes and hasn't expired."})
    except GitProviderError as e:
        return jsonify({"ok": False, "error": str(e)})
    record = _store().save(provider_name, token, expires_at=expires_at)
    return jsonify({"ok": True, "account": account, "status": _status(record)})


@git_bp.route("/integration", methods=["DELETE"])
def delete_integration():
    _store().clear()
    return jsonify({"ok": True})


@git_bp.route("/repos", methods=["GET"])
def list_repos():
    store = _store()
    record = store.load()
    if not record or not record.get("token"):
        return jsonify({"ok": False, "error": "No git provider connected."})
    try:
        provider = provider_for_name(record["provider"], record["token"])
        repos = provider.list_repos()
    except GitAuthError:
        # Phase 4 self-healing: flag the token so the UI can show a re-auth panel
        # without breaking existing (SSH/embedded) deployments.
        store.mark_invalid()
        return jsonify({"ok": False, "token_invalid": True,
                        "error": "Your access token has expired or been revoked."})
    except GitProviderError as e:
        return jsonify({"ok": False, "error": str(e)})
    store.mark_valid()
    return jsonify({"ok": True, "repos": repos})


@git_bp.route("/branches", methods=["GET"])
def list_branches():
    repo = request.args.get("repo", "").strip()
    if not repo:
        return jsonify({"ok": False, "error": "repo parameter is required."})
    store = _store()
    record = store.load()
    if not record or not record.get("token"):
        return jsonify({"ok": False, "error": "No git provider connected."})
    try:
        provider = provider_for_name(record["provider"], record["token"])
        branches = provider.list_branches(repo)
    except GitAuthError:
        store.mark_invalid()
        return jsonify({"ok": False, "token_invalid": True,
                        "error": "Your access token has expired or been revoked."})
    except GitProviderError as e:
        return jsonify({"ok": False, "error": str(e)})
    return jsonify({"ok": True, "branches": branches})
