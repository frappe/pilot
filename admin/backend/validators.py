from __future__ import annotations

import re

_APP_NAME_RE = re.compile(r'^[A-Za-z][A-Za-z0-9_\-]*$')
_BRANCH_RE = re.compile(r'^[A-Za-z0-9._/\-]+$')
_SITE_NAME_RE = re.compile(r'^[a-zA-Z0-9][a-zA-Z0-9\-\.]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$')
_CRON_RE = re.compile(
    r'^(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)$'
)
_EMAIL_RE = re.compile(r'^[^\s@]+@[^\s@]+\.[^\s@]+$')
_GIT_HTTP_RE = re.compile(r'^https?://.+')
_GIT_SSH_RE = re.compile(r'^git@.+:.+')
_GIT_LOCAL_RE = re.compile(r'^(/|~|\.\.?/).+')


def validate_app_name(name: str) -> str | None:
    if not name:
        return "App name is required."
    if not _APP_NAME_RE.match(name):
        return "App name must start with a letter and contain only letters, numbers, hyphens, and underscores."
    return None


def validate_repo_url(url: str) -> str | None:
    if not url:
        return "Repository URL is required."
    if not (_GIT_HTTP_RE.match(url) or _GIT_SSH_RE.match(url) or _GIT_LOCAL_RE.match(url)):
        return "Repository URL must be a valid git URL (https://, git@host:path, or a local path)."
    return None


def validate_branch_name(branch: str) -> str | None:
    if not branch:
        return None
    if ".." in branch:
        return "Branch name must not contain '..'."
    if branch.startswith("-") or branch.endswith("."):
        return "Branch name must not start with '-' or end with '.'."
    if not _BRANCH_RE.match(branch):
        return "Branch name may only contain letters, numbers, hyphens, underscores, dots, and slashes."
    return None


def validate_site_name(name: str) -> str | None:
    if not name:
        return "Site name is required."
    if len(name) > 253:
        return "Site name is too long (max 253 characters)."
    if not _SITE_NAME_RE.match(name):
        return "Site name must be a valid hostname (letters, numbers, hyphens, and dots only)."
    return None


def validate_cron_expression(expr: str) -> str | None:
    if not expr:
        return "Schedule expression is required."
    if not _CRON_RE.match(expr.strip()):
        return "Invalid cron expression. Expected 5 fields: minute hour day month weekday (e.g. '0 2 * * *')."
    return None


def validate_port(port: int, name: str = "Port") -> str | None:
    if not 1 <= port <= 65535:
        return f"{name} must be between 1 and 65535."
    return None


def validate_email(email: str) -> str | None:
    if not email:
        return None
    if not _EMAIL_RE.match(email):
        return "Invalid email address."
    return None


def validate_worker_count(n: int, name: str = "Worker count") -> str | None:
    if n < 1:
        return f"{name} must be at least 1."
    return None


def first_error(*errors: str | None) -> str | None:
    """Return the first non-None error, or None if all pass."""
    return next((e for e in errors if e), None)
