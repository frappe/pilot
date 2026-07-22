from __future__ import annotations

from pathlib import Path

from admin.backend.api.responses import error_response
from pilot.exceptions import TaskConflictError


def site_name(kwargs: dict) -> str:
    return kwargs["name"]


def site_not_found():
    return error_response("site_not_found", "Site not found.", 404)


def malformed_body():
    return error_response("malformed_body", "Request body must be a JSON object.", 400)


def invalid_fields():
    return error_response("invalid_fields", "One or more request fields are invalid.", 422)


def text_fields(data: dict, *names: str) -> dict[str, str] | None:
    fields = {}
    for name in names:
        value = data.get(name, "")
        if not isinstance(value, str):
            return None
        fields[name] = value.strip()
    return fields


def internal_error(message: str):
    return error_response("internal_error", message, 500)


def task_failure(error: Exception):
    if isinstance(error, TaskConflictError):
        return error_response("task_conflict", "A conflicting task is already active.", 409)
    if isinstance(error, ValueError):
        return error_response("invalid_task", str(error), 422)
    return internal_error("Could not start the requested task.")


def site_name_failure(message: str):
    if "already" in message or "clashes" in message:
        return error_response("site_name_conflict", "The site name is already in use.", 409)
    return error_response("invalid_site_name", message, 422)


def new_site_name_error(bench_root: Path, name: str) -> str | None:
    """Validate a new-site name before any task starts, so the error lands in the UI
    instead of failing mid-run. Mirrors NewSiteCommand._validate."""
    from pilot.config import BenchConfig
    from pilot.utils import host_owner, normalize_host

    sites_path = bench_root / "sites"
    if sites_path.is_symlink():
        return "Sites directory must not be a symbolic link."
    site_path = sites_path / name
    if site_path.is_symlink() or (site_path / "site_config.json").exists():
        return f"Site '{name}' already exists."

    owner = host_owner(bench_root, name)
    if owner:
        return f"'{name}' is already used by bench '{owner}' (as a site or its admin domain). All benches share one nginx, so hostnames must be unique."

    try:
        admin_domain = BenchConfig.read(bench_root).admin.domain
    except Exception:
        admin_domain = ""
    if admin_domain and normalize_host(name) == normalize_host(admin_domain):
        return f"Site '{name}' clashes with this bench's admin domain. An admin domain must not match a site domain."

    from pilot.core.adapters.domain_provider import DomainRouteProvider
    from pilot.utils import matches_wildcard

    patterns = DomainRouteProvider.wildcard_domains()
    if patterns and not matches_wildcard(name, patterns):
        return f"Site name must match one of this bench's wildcard domains: {', '.join(patterns)}."
    return None
