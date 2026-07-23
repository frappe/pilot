from __future__ import annotations

from flask import Response, stream_with_context

from admin.backend.api.v1.editor import editor_bp, with_workspace
from admin.backend.core.editor import events as events_service


@editor_bp.get("/events")
@with_workspace
def file_events(ws):
    return _sse(events_service.file_events(ws.root))


@editor_bp.get("/git/events")
@with_workspace
def git_change_events(ws):
    return _sse(events_service.git_events(ws.git))


def _sse(generator):
    return Response(
        stream_with_context(generator),
        mimetype="text/event-stream",
        headers={"X-Accel-Buffering": "no", "Cache-Control": "no-cache"},
    )
