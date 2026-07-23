from __future__ import annotations

import json
from pathlib import Path

from flask import current_app, jsonify

from admin.backend.api.responses import error_response
from admin.backend.api.v1.editor import editor_bp, json_body, with_workspace


@editor_bp.get("/state")
@with_workspace
def get_state(ws):
    path = _state_path(ws.root.name)
    if not path.exists():
        return jsonify({})
    return jsonify(json.loads(path.read_text() or "{}"))


@editor_bp.put("/state")
@with_workspace
def put_state(ws):
    state = json_body()
    if len(json.dumps(state)) > 1_000_000:
        return error_response("too_large", "State exceeds 1MB.", 413)
    path = _state_path(ws.root.name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state))
    return jsonify({"ok": True})


def _state_path(app_name: str) -> Path:
    return Path(current_app.config["BENCH_ROOT"]) / ".editor-state" / f"{app_name}.json"
