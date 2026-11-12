from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_REDACTED = "[redacted]"
_PRIVATE_ARGS = {
    "new-site-from-backup": frozenset({"db_file", "public_files", "private_files"}),
}
# Every task's args are matched against these, so a new command carrying a secret is
# covered the day it is added. wrapper.py redacts task output with the same list.
SENSITIVE_KEY_PARTS = (
    "password",
    "secret",
    "token",
    "credential",
    "access_key",
    "private_key",
)
_REPO_ARGS = frozenset({"repo", "repo_url"})
_BRANCH_ARGS = frozenset({"branch", "target_branch"})


def task_secret_args(command: str, args: dict) -> dict:
    """Args to keep in the task's private secrets file instead of its metadata."""
    return {key: value for key, value in args.items() if is_sensitive_key(key)}


def task_has_secrets(command: str, args: dict | None = None) -> bool:
    """Whether this task was given credentials, so a retry would need fresh ones.
    Reads the recorded args, where a secret is present but redacted."""
    return bool(task_secret_args(command, args or {}))


def is_sensitive_key(key: str) -> bool:
    """Whether an argument name looks like it holds a secret."""
    normalized = key.lower().replace("-", "_")
    return any(part in normalized for part in SENSITIVE_KEY_PARTS)


def public_task_args(command: str, args: dict) -> dict:
    """The arguments safe to store and show, without private paths or secrets."""
    private_args = _PRIVATE_ARGS.get(command, ())
    return redact_task_args({key: value for key, value in args.items() if key not in private_args})


def fingerprint_task_args(command: str, args: dict) -> dict:
    """The arguments that identify a request, with uploaded files reduced to a digest."""
    fingerprint_args = redact_task_args(args)
    if command != "new-site-from-backup":
        return fingerprint_args
    for key in _PRIVATE_ARGS[command]:
        if path := args.get(key):
            fingerprint_args[key] = {"sha256": _file_digest(path)}
    return fingerprint_args


def redact_task_args(args: dict) -> dict:
    """The arguments with secret values replaced, recursively."""
    return {key: _redact_value(key, value) for key, value in args.items()}


def reject_unsafe_git_args(args: dict) -> None:
    """A queued repo or branch ends up in git's argv, so hold it to the same rules the
    CLI and the wizard use - `ext::sh -c ...` must never reach that far."""
    from pilot.internal.validators import validate_branch_name, validate_repo_url

    for key, value in args.items():
        if not isinstance(value, str) or not value:
            continue
        if key in _REPO_ARGS and (error := validate_repo_url(value)):
            raise ValueError(error)
        if key in _BRANCH_ARGS and (error := validate_branch_name(value)):
            raise ValueError(error)


def reject_url_credentials(value) -> None:
    """Raise when any URL in the arguments carries a user name or password."""
    if isinstance(value, dict):
        for child in value.values():
            reject_url_credentials(child)
    elif isinstance(value, list):
        for child in value:
            reject_url_credentials(child)
    elif isinstance(value, str) and _has_url_credentials(value):
        raise ValueError("Credentials in repository URLs are not allowed; use the Git provider connection.")


def _redact_value(key: str, value):
    """One value with secrets and URL credentials removed."""
    if is_sensitive_key(key):
        return _REDACTED
    if isinstance(value, dict):
        return redact_task_args(value)
    if isinstance(value, list):
        return [_redact_value("", item) for item in value]
    if isinstance(value, str):
        return _without_url_credentials(value)
    return value


def _without_url_credentials(value: str) -> str:
    """The URL without its user name and password."""
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or parsed.username is None:
            return value
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, host, parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return value


def _has_url_credentials(value: str) -> bool:
    """Whether an http or https URL carries credentials."""
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and parsed.username is not None
    except ValueError:
        return False


def _file_digest(path: str | Path) -> str:
    """SHA-256 of a file, read in chunks."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
