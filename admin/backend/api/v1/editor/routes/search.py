from __future__ import annotations

from flask import jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.editor import editor_bp, json_body, query_flag, with_workspace
from admin.backend.core.editor import search as search_service


@editor_bp.get("/search")
@with_workspace
def search_files(ws):
    return jsonify(
        search_service.search(
            ws.root, request.args.get("q", ""), query_flag("regex"), query_flag("word"), query_flag("case")
        )
    )


@editor_bp.post("/replace")
@with_workspace
def replace_files(ws):
    body = json_body()
    if not body.get("query"):
        return error_response("bad_request", "Query is required.", 400)
    changed = search_service.replace(
        ws,
        body["query"],
        body.get("replace", ""),
        bool(body.get("regex")),
        bool(body.get("case")),
        bool(body.get("word")),
        body.get("files", []),
    )
    return jsonify({"changed": changed})
