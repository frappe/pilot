from __future__ import annotations

import hashlib
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

_REDACTED = "[redacted]"
_SECRET_ARGS = {
    "new-site": frozenset({"admin_password"}),
    "new-site-from-backup": frozenset({"admin_password"}),
    "reinstall-site": frozenset({"admin_password"}),
}
_PRIVATE_ARGS = {
    "new-site-from-backup": frozenset(
        {"db_file", "public_files", "private_files"}
    ),
}
_SENSITIVE_KEY_PARTS = ("password", "secret", "token", "credential", "private_key")


def task_secret_args(command: str, args: dict) -> dict:
    return {key: args[key] for key in _SECRET_ARGS.get(command, ()) if key in args}


def task_requires_secrets(command: str) -> bool:
    return bool(_SECRET_ARGS.get(command))


def public_task_args(command: str, args: dict) -> dict:
    private_args = _PRIVATE_ARGS.get(command, ())
    return redact_task_args(
        {key: value for key, value in args.items() if key not in private_args}
    )


def fingerprint_task_args(command: str, args: dict) -> dict:
    fingerprint_args = redact_task_args(args)
    if command != "new-site-from-backup":
        return fingerprint_args
    for key in _PRIVATE_ARGS[command]:
        if path := args.get(key):
            fingerprint_args[key] = {"sha256": _file_digest(path)}
    return fingerprint_args


def redact_task_args(args: dict) -> dict:
    return {key: _redact_value(key, value) for key, value in args.items()}


def reject_url_credentials(value) -> None:
    if isinstance(value, dict):
        for child in value.values():
            reject_url_credentials(child)
    elif isinstance(value, list):
        for child in value:
            reject_url_credentials(child)
    elif isinstance(value, str) and _has_url_credentials(value):
        raise ValueError(
            "Credentials in repository URLs are not allowed; use the Git provider connection."
        )


def _redact_value(key: str, value):
    normalized = key.lower().replace("-", "_")
    if any(part in normalized for part in _SENSITIVE_KEY_PARTS):
        return _REDACTED
    if isinstance(value, dict):
        return redact_task_args(value)
    if isinstance(value, list):
        return [_redact_value("", item) for item in value]
    if isinstance(value, str):
        return _without_url_credentials(value)
    return value


def _without_url_credentials(value: str) -> str:
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
    try:
        parsed = urlsplit(value)
        return parsed.scheme in ("http", "https") and parsed.username is not None
    except ValueError:
        return False


def _file_digest(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
