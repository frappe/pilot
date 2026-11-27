from __future__ import annotations

from dataclasses import asdict

from flask import Blueprint, jsonify

from admin.backend.api.requests import ApiRequest
from admin.backend.api.responses import error_response, no_content_response
from pilot.core.bench.site_templates import InvalidSiteTemplateError, SiteTemplateNotFoundError

site_templates_bp = Blueprint("site_templates", __name__)


@site_templates_bp.get("")
def list_templates():
    """List the app selections saved for new sites."""
    templates = ApiRequest.get_bench().site_templates.all
    return jsonify({"templates": [asdict(template) for template in templates]})


@site_templates_bp.put("/<name>")
def save_template(name: str):
    """Save an app selection under the name, replacing a template that already has it."""
    apps = ApiRequest.get_json_body().get("apps")
    if not isinstance(apps, list) or not all(isinstance(app, str) for app in apps):
        return error_response("invalid_site_template", "apps must be a list of app names.", 422)
    try:
        template = ApiRequest.get_bench().site_templates.save(name.strip(), [app.strip() for app in apps])
    except InvalidSiteTemplateError as error:
        return error_response("invalid_site_template", str(error), 422)
    return jsonify(asdict(template))


@site_templates_bp.delete("/<name>")
def remove_template(name: str):
    """Remove a saved app selection."""
    try:
        ApiRequest.get_bench().site_templates.remove(name)
    except SiteTemplateNotFoundError:
        return error_response("site_template_not_found", "Site template was not found.", 404)
    return no_content_response()
