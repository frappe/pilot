from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

_APP_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_\-]*$")
_BRANCH_RE = re.compile(r"^[A-Za-z0-9._/\-]+$")
_SITE_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-\.]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$")
_CRON_RE = re.compile(
    r"^(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)$"
)
_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_GIT_HTTP_RE = re.compile(r"^https?://.+")
_GIT_SSH_RE = re.compile(r"^git@.+:.+")
_GIT_LOCAL_RE = re.compile(r"^(/|~|\.\.?/).+")
_METADATA_HOSTNAMES = frozenset({"metadata", "metadata.google.internal"})
_AWS_METADATA_IPV6 = ipaddress.ip_address("fd00:ec2::254")
_LOWERCASE_RE = re.compile(r"[a-z]")
_UPPERCASE_RE = re.compile(r"[A-Z]")
_DIGIT_RE = re.compile(r"\d")
_SYMBOL_RE = re.compile(r"[^A-Za-z0-9]")


def validate_app_name(name: str) -> str | None:
    if not name:
        return "App name is required."
    if not _APP_NAME_RE.match(name):
        return (
            "App name must start with a letter and contain only letters, numbers, hyphens, and underscores."
        )
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


ADMIN_PASSWORD_REQUIREMENTS = (
    "at least 8 characters, with upper and lower case letters, a number, and a symbol"
)


def validate_admin_password(password: str) -> str | None:
    """Mirror of the dashboard's PASSWORD_REQUIREMENTS (utils/passwordStrength.js)."""
    from pilot.internal.password_hash import is_hashed

    if is_hashed(password):
        return "Password must not be a stored password hash."
    if len(password) < 8:
        return "Password must be at least 8 characters."
    if not (_LOWERCASE_RE.search(password) and _UPPERCASE_RE.search(password)):
        return "Password must contain upper and lower case letters."
    if not _DIGIT_RE.search(password):
        return "Password must contain at least one number."
    if not _SYMBOL_RE.search(password):
        return "Password must contain at least one symbol."
    return None


def validate_external_url(url: str, field: str = "URL") -> str | None:
    """Refuse a URL this server must never fetch: a non-HTTP scheme, embedded
    credentials, or a host that names the cloud metadata service. Literal hosts only -
    a domain that resolves to a blocked address is not caught here."""
    if not url:
        return None
    if url != url.strip() or any(character.isspace() for character in url):
        return f"{field} must not contain spaces."
    parsed = urlsplit(url)
    if parsed.scheme not in ("http", "https"):
        return f"{field} must start with http:// or https://."
    if parsed.username or parsed.password:
        return f"{field} must not embed credentials."
    host = parsed.hostname
    if not host:
        return f"{field} must include a host."
    if host in _METADATA_HOSTNAMES or _is_metadata_address(host):
        return f"{field} must not point at a link-local or metadata address."
    return None


def _is_metadata_address(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_link_local or address == _AWS_METADATA_IPV6


def validate_email(email: str) -> str | None:
    if not email:
        return None
    if not _EMAIL_RE.match(email):
        return "Invalid email address."
    return None
