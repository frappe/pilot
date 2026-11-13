from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit


def validate_app_name(name: str) -> str | None:
    """An error message for an invalid app name, or None."""
    if not name:
        return "App name is required."
    if not re.match(r"^[A-Za-z][A-Za-z0-9_\-]*$", name):
        return (
            "App name must start with a letter and contain only letters, numbers, hyphens, and underscores."
        )
    return None


def validate_repo_url(url: str) -> str | None:
    """An error message for an invalid git repository URL, or None."""
    if not url:
        return "Repository URL is required."
    if not (
        re.match(r"^https?://.+", url) or re.match(r"^git@.+:.+", url) or re.match(r"^(/|~|\.\.?/).+", url)
    ):
        return "Repository URL must be a valid git URL (https://, git@host:path, or a local path)."
    return None


def validate_branch_name(branch: str) -> str | None:
    """An error message for an unsafe branch name, or None; an empty name is allowed."""
    if not branch:
        return None
    if ".." in branch:
        return "Branch name must not contain '..'."
    if branch.startswith("-") or branch.endswith("."):
        return "Branch name must not start with '-' or end with '.'."
    if not re.match(r"^[A-Za-z0-9._/\-]+$", branch):
        return "Branch name may only contain letters, numbers, hyphens, underscores, dots, and slashes."
    return None


def validate_hostname(name: str, label: str = "Hostname") -> str | None:
    """An error message for an invalid hostname, or None."""
    if not name:
        return f"{label} is required."
    if len(name) > 253:
        return f"{label} is too long (max 253 characters)."
    if not re.match(r"^[a-zA-Z0-9][a-zA-Z0-9\-\.]*[a-zA-Z0-9]$|^[a-zA-Z0-9]$", name):
        return f"{label} must be a valid hostname (letters, numbers, hyphens, and dots only)."
    return None


def validate_hostname_pattern(pattern: str, label: str = "Hostname pattern") -> str | None:
    """A VM hostname glob, where `*` stands for the runtime-assigned VM id."""
    if pattern.count("*") != 1:
        return f"{label} must contain exactly one '*' for the VM id, as in 'site-*.example.com'."
    return validate_hostname(pattern.replace("*", "vm"), label)


def validate_site_name(name: str) -> str | None:
    """An error message for an invalid site name, or None."""
    return validate_hostname(name, "Site name")


def validate_cron_expression(expr: str) -> str | None:
    """An error message for an invalid five-field cron expression, or None."""
    if not expr:
        return "Schedule expression is required."
    if not re.match(
        r"^(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)\s+(\*|[0-9,\-*/]+)$",
        expr.strip(),
    ):
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
    if not (re.search(r"[a-z]", password) and re.search(r"[A-Z]", password)):
        return "Password must contain upper and lower case letters."
    if not re.search(r"\d", password):
        return "Password must contain at least one number."
    if not re.search(r"[^A-Za-z0-9]", password):
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
    if host in frozenset({"metadata", "metadata.google.internal"}) or _is_metadata_address(host):
        return f"{field} must not point at a link-local or metadata address."
    return None


def _is_metadata_address(host: str) -> bool:
    """Whether the host is a link-local or cloud metadata address."""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_link_local or address == ipaddress.ip_address("fd00:ec2::254")


def validate_email(email: str) -> str | None:
    """An error message for an invalid email address, or None; an empty address is allowed."""
    if not email:
        return None
    if not re.match(r"^[^\s@]+@[^\s@]+\.[^\s@]+$", email):
        return "Invalid email address."
    return None
