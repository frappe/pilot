from __future__ import annotations

from flask import jsonify, request

from admin.backend.api.responses import error_response
from admin.backend.api.v1.editor import editor_bp, json_body, query_flag, with_workspace


@editor_bp.get("/git/status")
@with_workspace
def git_status(ws):
    return jsonify(ws.git.status())


@editor_bp.get("/git/blame")
@with_workspace
def git_blame(ws):
    return jsonify({"lines": ws.git.blame(ws.rel(ws.safe(request.args.get("path", ""))))})


@editor_bp.get("/git/diff")
@with_workspace
def git_diff(ws):
    return jsonify(ws.git.diff_gutters(ws.rel(ws.safe(request.args.get("path", "")))))


@editor_bp.get("/git/show")
@with_workspace
def git_show(ws):
    return jsonify({"content": ws.git.show_head(ws.rel(ws.safe(request.args.get("path", ""))))})


@editor_bp.get("/git/branches")
@with_workspace
def git_branches(ws):
    return jsonify(ws.git.branches())


@editor_bp.post("/git/checkout")
@with_workspace
def git_checkout(ws):
    body = json_body()
    if not body.get("branch"):
        return error_response("bad_request", "Branch is required.", 400)
    return _git_result(*ws.git.checkout(body["branch"], bool(body.get("create"))))


@editor_bp.post("/git/commit")
@with_workspace
def git_commit(ws):
    body = json_body()
    if not (body.get("message") or "").strip():
        return error_response("bad_request", "Commit message is required.", 400)
    return _git_result(*ws.git.commit(body["message"], bool(body.get("all"))))


@editor_bp.get("/git/filediff")
@with_workspace
def git_filediff(ws):
    rel = ws.rel(ws.safe(request.args.get("path", "")))
    return jsonify(ws.git.file_diff(ws, rel, query_flag("staged")))


@editor_bp.get("/git/log")
@with_workspace
def git_log(ws):
    skip = request.args.get("skip", type=int) or 0
    limit = request.args.get("limit", type=int) or 50
    if limit <= 0 or limit > 200:
        limit = 50
    return jsonify(ws.git.log(skip, limit))


@editor_bp.get("/git/commit-info")
@with_workspace
def git_commit_info(ws):
    sha = request.args.get("sha", "")
    if not ws.git.is_sha(sha):
        return error_response("bad_request", "Invalid commit sha.", 400)
    info = ws.git.commit_info(sha)
    if info is None:
        return error_response("not_found", "Commit not found.", 404)
    return jsonify(info)


@editor_bp.get("/git/commit-diff")
@with_workspace
def git_commit_diff(ws):
    sha = request.args.get("sha", "")
    if not ws.git.is_sha(sha):
        return error_response("bad_request", "Invalid commit sha.", 400)
    rel = ws.rel(ws.safe(request.args.get("path", "")))
    return jsonify({"diff": ws.git.commit_diff(sha, rel)})


@editor_bp.post("/git/stage")
@with_workspace
def git_stage(ws):
    return _git_path_op(ws, ws.git.stage)


@editor_bp.post("/git/unstage")
@with_workspace
def git_unstage(ws):
    return _git_path_op(ws, ws.git.unstage)


@editor_bp.post("/git/discard")
@with_workspace
def git_discard(ws):
    path = (json_body().get("path") or "").strip()
    if not path:
        return error_response("bad_request", "Path is required.", 400)
    return _git_result(*ws.git.discard(ws, ws.rel(ws.safe(path))))


@editor_bp.post("/git/apply")
@with_workspace
def git_apply(ws):
    body = json_body()
    if not body.get("patch"):
        return error_response("bad_request", "Patch is required.", 400)
    return _git_result(*ws.git.apply_patch(body["patch"], bool(body.get("cached")), bool(body.get("reverse"))))


@editor_bp.post("/git/push")
@with_workspace
def git_push(ws):
    return _git_result(*ws.git.push(bool(json_body().get("force"))))


@editor_bp.post("/git/pull")
@with_workspace
def git_pull(ws):
    return _git_result(*ws.git.pull())


def _git_path_op(ws, op):
    path = (json_body().get("path") or "").strip()
    if not path:
        return error_response("bad_request", "Path is required.", 400)
    return _git_result(*op(ws.rel(ws.safe(path))))


def _git_result(ok: bool, message: str):
    response = jsonify({"ok": ok, "message": message})
    response.status_code = 200 if ok else 409
    return response
