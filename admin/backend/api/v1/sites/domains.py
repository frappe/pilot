from __future__ import annotations

import json
from pathlib import Path

from flask import current_app, jsonify, request

from pilot.exceptions import BenchError, ConfigError, DomainConflictError, DomainProviderError
from pilot.internal.site_paths import site_config_path, site_exists
from pilot.internal.validators import validate_email, validate_site_name
from pilot.tasks.manager.task_runner import TaskCallback, TaskRunner

from admin.backend.api.responses import accepted_task_response, error_response
from admin.backend.middleware import require_scope

from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import (
    internal_error,
    invalid_fields,
    malformed_body,
    site_name,
    site_not_found,
    task_failure,
    text_fields,
)


@sites_bp.post("/<name>/actions/enable-tls")
@require_scope(site_name)
def enable_tls(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    config_path = site_config_path(bench_root, name)
    if config_path is None:
        return site_not_found()

    from pilot.config.toml_store import BenchTomlStore

    store = BenchTomlStore.for_bench(bench_root)
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    elif not isinstance(data, dict):
        return malformed_body()
    fields = text_fields(data, "email")
    if fields is None:
        return invalid_fields()
    email = fields["email"]
    if email:
        if err := validate_email(email):
            return error_response(
                "invalid_email", err, 422, {"needs_email": True}
            )
    else:
        try:
            config = store.read()
        except Exception:
            return internal_error("Could not read certificate configuration.")

    if not email and not config.letsencrypt.email:
        return error_response(
            "missing_certificate_email",
            "A Let's Encrypt account email is required to issue certificates.",
            422,
            {"needs_email": True},
        )

    try:
        current = json.loads(config_path.read_text())
    except Exception:
        return internal_error("Could not read the site configuration.")
    if not isinstance(current, dict):
        return internal_error("Could not read the site configuration.")
    if current.get("ssl"):
        return error_response("tls_already_enabled", "TLS is already enabled.", 409)

    rollback: TaskCallback = {"operation": "disable-site-ssl", "args": {"site": name}}
    task_args = {"site": name}
    if email:
        task_args["email"] = email
    try:
        task_id = TaskRunner(bench_root).run(
            "setup-letsencrypt",
            task_args,
            callbacks={
                "on_failure": rollback,
                "on_cancel": rollback,
            },
            idempotency_key=request.headers.get("Idempotency-Key"),
            resource_key=f"site:{name.lower()}",
        )
    except Exception as error:
        return task_failure(error)
    return accepted_task_response(bench_root, task_id)


def _domain_routes(bench_root: Path):
    from pilot.config.toml_store import BenchTomlStore
    from pilot.core.bench import Bench
    from pilot.core.domains import DomainRouteProvider

    bench = Bench(BenchTomlStore.for_bench(bench_root).read(), bench_root)
    return DomainRouteProvider(bench)


def _apply_domains(bench_root: Path, name: str) -> str:
    """Re-run the right task so nginx (and certs, for SSL sites) pick up the change."""
    ssl = bool(json.loads((bench_root / "sites" / name / "site_config.json").read_text()).get("ssl"))
    return TaskRunner(bench_root).run("setup-letsencrypt" if ssl else "setup-nginx", {})


@sites_bp.route("/<name>/domains", methods=["GET"])
@require_scope(site_name)
def list_domains(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    try:
        routes = _domain_routes(bench_root)
        return jsonify({"domains": routes.domains(name), "primary": routes.primary(name)})
    except BenchError as error:
        return _domain_failure(error, "Could not read site domains.")
    except Exception:
        return internal_error("Could not read site domains.")


@sites_bp.get("/<name>/domains/<domain>/dns-records")
@require_scope(site_name)
def domain_dns_records(name: str, domain: str):
    """Read-only guidance for attaching a domain: CNAME/A record options."""
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    if err := validate_site_name(domain):
        return error_response("invalid_domain", err, 422)
    try:
        records = _domain_routes(bench_root).generate_dns_records(name, domain)
    except BenchError as error:
        return _domain_failure(error, "Could not generate DNS records.")
    except Exception:
        return internal_error("Could not generate DNS records.")
    return jsonify(records)


@sites_bp.post("/<name>/domains")
@require_scope(site_name)
def add_domain(name: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    fields = text_fields(data, "domain")
    if fields is None:
        return invalid_fields()
    domain = fields["domain"]
    if err := validate_site_name(domain):
        return error_response("invalid_domain", err, 422)
    try:
        _domain_routes(bench_root).register(name, domain)
        task_id = _apply_domains(bench_root, name)
    except BenchError as error:
        return _domain_failure(error, "Could not attach the domain.")
    except Exception:
        return internal_error("Could not attach the domain.")
    return accepted_task_response(bench_root, task_id)


@sites_bp.get("/<name>/domains/<domain>")
@require_scope(site_name)
def get_domain(name: str, domain: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    if err := validate_site_name(domain):
        return error_response("invalid_domain", err, 422)
    try:
        attached, is_primary = _domain_status(_domain_routes(bench_root), name, domain)
    except BenchError as error:
        return _domain_failure(error, "Could not read the domain.")
    except Exception:
        return internal_error("Could not read the domain.")
    if not attached:
        return error_response("domain_not_found", "Domain not found.", 404)
    return jsonify({"domain": domain, "is_primary": is_primary})


@sites_bp.patch("/<name>/domains/<domain>")
@require_scope(site_name)
def update_domain(name: str, domain: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    if err := validate_site_name(domain):
        return error_response("invalid_domain", err, 422)
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("primary") is not True:
        return error_response(
            "invalid_fields", 'Only setting {"primary": true} is supported.', 422
        )
    try:
        _domain_routes(bench_root).set_primary(name, domain)
        task_id = _apply_domains(bench_root, name)
    except BenchError as error:
        return _domain_failure(error, "Could not change the primary domain.")
    except Exception:
        return internal_error("Could not change the primary domain.")
    # nginx redirects non-primary hosts to the primary, so regenerate it.
    return accepted_task_response(bench_root, task_id)


@sites_bp.delete("/<name>/domains/<domain>")
@require_scope(site_name)
def remove_domain(name: str, domain: str):
    bench_root = Path(current_app.config["BENCH_ROOT"])
    if not site_exists(bench_root, name):
        return site_not_found()
    if err := validate_site_name(domain):
        return error_response("invalid_domain", err, 422)
    try:
        _domain_routes(bench_root).deregister(name, domain)
        task_id = _apply_domains(bench_root, name)
    except BenchError as error:
        return _domain_failure(error, "Could not detach the domain.")
    except Exception:
        return internal_error("Could not detach the domain.")
    return accepted_task_response(bench_root, task_id)


def _domain_status(routes, site_name: str, domain: str) -> tuple[bool, bool]:
    from pilot.utils import normalize_host

    normalized = normalize_host(domain)
    primary = routes.primary(site_name)
    if normalized == normalize_host(site_name):
        return True, not primary or normalize_host(primary) == normalized
    attached = normalized in {normalize_host(d) for d in routes.domains(site_name)}
    return attached, bool(primary) and normalize_host(primary) == normalized


def _domain_conflict():
    return error_response(
        "domain_conflict", "The domain conflicts with the current site state.", 409
    )


def _domain_failure(error: BenchError, message: str):
    if isinstance(error, ConfigError):
        raise error
    if isinstance(error, DomainConflictError):
        return _domain_conflict()
    if isinstance(error, DomainProviderError):
        return error_response(
            "domain_provider_unavailable",
            "The domain provider is unavailable.",
            503,
        )
    return internal_error(message)
