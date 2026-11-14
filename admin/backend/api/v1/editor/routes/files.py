from __future__ import annotations

from flask import jsonify, request

from admin.backend.api.v1.editor import editor_bp, json_body, with_workspace


@editor_bp.get("/tree")
@with_workspace
def tree(ws):
    """List a folder."""
    return jsonify(ws.tree(request.args.get("path", "")))


@editor_bp.get("/file")
@with_workspace
def read_file(ws):
    """Read a file with its ETag."""
    result = ws.read(request.args.get("path", ""))
    response = jsonify(result)
    response.headers["ETag"] = result["etag"]
    return response


@editor_bp.put("/file")
@with_workspace
def save_file(ws):
    """Save a file if its ETag matches."""
    status, result = ws.save(
        request.args.get("path", ""), json_body().get("content", ""), request.headers.get("If-Match", "*")
    )
    response = jsonify(result)
    response.status_code = status
    response.headers["ETag"] = result["etag"]
    return response


@editor_bp.post("/create")
@with_workspace
def create(ws):
    """Create a file or folder."""
    body = json_body()
    return jsonify(ws.create(body.get("path", ""), body.get("type", "file")))


@editor_bp.post("/rename")
@with_workspace
def rename(ws):
    """Rename a file or folder."""
    body = json_body()
    return jsonify(ws.rename(body.get("from", ""), body.get("to", "")))


@editor_bp.delete("/delete")
@with_workspace
def delete(ws):
    """Delete a file or folder."""
    ws.delete(request.args.get("path", ""))
    return jsonify({"ok": True})


@editor_bp.get("/files")
@with_workspace
def files(ws):
    """List every file."""
    return jsonify(ws.list_files())
