from __future__ import annotations

import codecs
import json
from collections.abc import Callable, Iterator
from typing import TypedDict

from flask import Response, current_app, jsonify, request, stream_with_context

from admin.backend.api.responses import error_response
from admin.backend.api.v1.sites import sites_bp
from admin.backend.api.v1.sites.shared import malformed_body, site_not_found
from admin.backend.draining import is_draining
from pilot.core.bench import Bench
from pilot.core.site.console.completions import ConsoleCompletions
from pilot.core.site.console.script import SiteScript
from pilot.exceptions import CommandError, ScriptBusyError
from pilot.internal.site_paths import site_exists


class ConsoleOutputEvent(TypedDict):
    output: str


class ConsoleExitEvent(TypedDict):
    exit_code: int
    timed_out: bool


class ConsoleErrorEvent(TypedDict):
    error: str


class DocTypeField(TypedDict):
    fieldname: str
    fieldtype: str
    label: str
    options: str


class ConsoleDocType(TypedDict):
    fields: list[DocTypeField]
    controller: str


@sites_bp.post("/<name>/console")
def run_console_script(name: str):
    """Run a Python script with Frappe connected to the site and stream its output as events. Needs a bench session; one script runs at a time."""
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return malformed_body()
    code, commit = data.get("code"), data.get("commit", False)
    if not isinstance(code, str) or not isinstance(commit, bool):
        return error_response("invalid_fields", "Send `code` as text and `commit` as true or false.", 422)

    bench = Bench(current_app.config["BENCH_ROOT"])
    if not site_exists(bench.path, name):
        return site_not_found()
    try:
        script = bench.site(name).script(code, commit)
    except ValueError as error:
        return error_response("invalid_script", str(error), 422)

    bench.audit_action("console_script", {"site": name, "commit": commit, "code": code})
    return Response(
        stream_with_context(_stream_script_events(script)),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )


@sites_bp.get("/<name>/console/doctypes")
def get_console_doctypes(name: str):
    """Doctype names for the console editor, read from the site."""
    return _suggestions_response(name, lambda completions: completions.doctypes)


@sites_bp.get("/<name>/console/doctypes/<doctype>")
def get_console_doctype(name: str, doctype: str):
    """Fields and controller class path of one doctype, loaded when the console editor first needs them."""
    return _suggestions_response(name, lambda completions: completions.get_doctype(doctype))


def _suggestions_response(name: str, read: Callable[[ConsoleCompletions], object]):
    bench = Bench(current_app.config["BENCH_ROOT"])
    if not site_exists(bench.path, name):
        return site_not_found()
    try:
        return jsonify(read(bench.site(name).console_completions))
    except ValueError as error:
        return error_response("completion_not_found", str(error), 404)
    except CommandError:
        return error_response("completions_unavailable", "Could not load suggestions from the site.", 503)


def _stream_script_events(script: SiteScript) -> Iterator[str]:
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    try:
        for chunk in script.run(should_stop=is_draining):
            if chunk is None:
                yield ": heartbeat\n\n"
            elif text := decoder.decode(chunk):
                yield _format_event(ConsoleOutputEvent(output=text))
    except ScriptBusyError as error:
        yield _format_event(ConsoleErrorEvent(error=str(error)))
        return
    if tail := decoder.decode(b"", final=True):
        yield _format_event(ConsoleOutputEvent(output=tail))
    yield _format_event(ConsoleExitEvent(exit_code=script.exit_code, timed_out=script.is_timed_out))


def _format_event(payload: dict) -> str:
    return f"data: {json.dumps(payload)}\n\n"
